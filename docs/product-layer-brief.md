# Product layer — build brief (Phase 1, Build A)

Hand this to Claude Code. Read CLAUDE.md, docs/content-engine-brief.md,
docs/quality-loop-brief.md, and docs/dashboard-brief.md first.

## Goal
Add a **product** layer between the org and everything else. A product is a child of the org
with its own profile (positioning, persona, value props, etc.). All existing downstream
objects (runs, artifacts, briefs, content drafts, metric_points, suggestions, report_uploads,
proposals) gain an OPTIONAL `product_id` so they can be scoped to a product OR remain
org-level when no product is selected. Brand voice, banned claims, rubric, conversion goal,
and default UTMs inherit from the org by default; products may override them. Product-only
fields (positioning, target persona, value props, proof points, differentiators, key features,
use cases, product competitors) live ONLY on the product profile.

This build is structural. NO document ingestion (next build). NO new UI beyond what's needed
to exercise the layer. UI polish is deliberately deferred — Replit will handle that. Build
the guts: schema, inheritance resolution, scoping, agents reading the resolved profile.

## The model

### ProductProfile (new table)
- `id` (uuid/int per convention)
- `org_id` (FK, scoped) — every product belongs to an org
- `name` (str, required) — e.g. "SimpleLegal CLM"
- `slug` (str, unique within org) — for URLs / UTM use
- `status` (enum: draft | confirmed) — same draft-then-confirm pattern as OrgProfile
- `website_url` (optional)
- **Product-only fields** (no inheritance — blank by default, filled by the user or, later,
  document ingestion):
  - `positioning` (text)
  - `target_persona` (text/JSON — role, seniority, pain points)
  - `value_props` (JSON list of strings)
  - `proof_points` (JSON list — case studies, stats, testimonials when filled)
  - `differentiators` (JSON list)
  - `key_features` (JSON list)
  - `use_cases` (JSON list)
  - `product_competitors` (JSON list — distinct from org-level competitors)
- **Inheritable overrides** (NULL = inherit from org; non-null = product overrides):
  - `brand_voice_override` (text or null)
  - `banned_claims_override` (JSON list or null)
  - `conversion_goal_override` (text or null)
  - `rubric_override` (JSON or null)
  - `utm_source_default` (str or null) — overrides org default UTM source for content under this product
  - `utm_medium_default` (str or null)
- `created_at`, `updated_at`

Mirror in `sql/schema.sql` with `org_id`-based RLS exactly like other tenant tables.

### Resolved profile (the inheritance function)
Add `resolve_product_profile(org_id, product_id) -> ResolvedProfile`:
- Loads OrgProfile via scoped() and (if product_id) ProductProfile via scoped().
- Returns a single merged view where every consumable field has a value:
  - Inheritable fields: product override if set, else org value, else blank.
  - Product-only fields: from product profile, blank if no product.
  - Carry provenance per field (`org` | `product` | `product_override`) — same honesty pattern
    as the Setup merge: the caller can see where each value came from.
- This is the ONLY way agents should read profile-level config. No agent should hit OrgProfile
  or ProductProfile directly — they call this resolver via ctx.
- If `product_id` is None, the resolver returns the org-level view unchanged (backwards-compat
  with everything already shipped).

### Adding product_id everywhere (optional, nullable)
Add `product_id` (nullable FK to product_profiles, indexed) to:
- `runs`
- `artifacts` (content_draft, briefs, content_idea, etc.)
- `proposals`
- `metric_points`
- `suggestions`
- `report_uploads`
NULL means "org-level / unscoped" — preserves all existing data and behavior. Existing rows
stay valid; nothing breaks.

## Agent + worker changes

### Worker
- The worker passes `product_id` into AgentContext (read from the run's `product_id`, may be
  None). It then calls `resolve_product_profile(org_id, product_id)` and places the
  ResolvedProfile on ctx (e.g. `ctx.profile`) instead of the raw OrgProfile.
- Existing `ctx.org_profile` accessor stays for backwards compat but is now derived from the
  resolved profile (the org layer of it).

### Agents
- `market_intel`, `content_engine`, and the dashboard suggestion engine all read
  `ctx.profile` (resolved) — not the raw OrgProfile. They get whatever's effective for the
  product (or the org if no product). No agent reasoning changes; the inputs are richer.
- Provenance lines that agents emit (citations / "grounded in...") should mention the product
  when one is selected ("grounded in your SimpleLegal product profile + org profile").
- Content engine: when a product_id is set, prefer product_competitors over org-level
  competitors in positioning copy; use the product's positioning as the lead frame. Brand
  voice, banned_claims, conversion_goal come from the resolved profile (so inheritance
  "just works").

### Run creation
- API endpoints that create runs (market_intel, content_engine, etc.) accept an optional
  `product_id`. When omitted, runs are org-level (current behavior).
- UTM tagging at content creation uses the product's utm_source/medium defaults if set, else
  the org's, else the content-type default. utm_campaign continues to slug from topic, but
  if a product_id is set, prefix the campaign slug with the product slug
  (e.g. `simplelegal-clm__clm-comparison-q2`) so reports naturally segment by product.

## API additions
- `POST /api/products` — create a product profile (draft).
- `GET /api/products` — list products for the current org (scoped).
- `GET /api/products/{id}` — fetch one (scoped).
- `PATCH /api/products/{id}` — edit fields, including setting overrides to null to revert to
  inheritance. Scoped + tenant-isolated.
- `POST /api/products/{id}/confirm` — flip draft → confirmed (mirrors OrgProfile).
- `DELETE /api/products/{id}` — soft delete or hard delete per existing convention; cascade
  is NOT required because product_id is nullable everywhere (deleted product → existing
  artifacts/runs become org-level, which is acceptable v1 behavior; document this).
- Run-creation endpoints accept `product_id`.
- `GET /api/profile/resolved?product_id=...` — returns the ResolvedProfile (useful for
  debugging and for the UI to show "what's effective right now"); scoped.

## Minimal UI (app/static/ui.html) — guts only, no polish
- Setup tab gets a **Products** section: list of products with status, a "New product" button
  opening a basic form (name, slug, website, positioning, persona, value_props, key_features,
  use_cases, product_competitors; collapsible "Inheritable overrides" section for the
  override fields with a clear "leave blank to inherit from org" label).
- A simple **product selector** (dropdown) near the top of Runs / Content / Dashboard tabs.
  When a product is selected:
  - Runs created from that page get the product_id.
  - The Content tab generates content scoped to that product.
  - The Dashboard view filters to that product's metric_points / suggestions.
  - When `(none)` is selected, everything stays org-level (current behavior).
- That's it. No styling investment, no nested navigation, no breadcrumbs, no polish. Functional
  controls only. UI polish is deferred to Replit.

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` for ALL reads/writes (ProductProfile, all newly-added product_id
  filters). No bare selects. Cross-org reads must fail closed in tests.
- Agents read profile via the resolver through ctx — never OrgProfile/ProductProfile directly.
- Reuse the additive-merge / precedence pattern from the OrgProfile Setup work; don't
  reinvent the inheritance logic — it's the same shape (resolve highest-precedence non-null).
- Schema change. Mirror in sql/schema.sql with RLS for product_profiles AND ensure the new
  product_id columns are part of the existing tables' RLS predicates (no policy gaps).
- On SQLite: delete agenthq.db + re-run scripts/seed.py so create_all() rebuilds; restart
  uvicorn AND worker. Smoke uses its own DB (don't rm while uvicorn runs).
- Seed: add ONE example product under Onit (e.g. "SimpleLegal CLM" with positioning + a few
  value props filled in) so the dev DB has a working example without us hand-creating one.
- Don't touch the TS app or .replit's [deployment] block.
- Keep `python -m scripts.smoke` green. ADD tests (hermetic — stub the LLM):
  (1) Create + confirm a product profile; product-only fields persist; tenant isolation holds
      (Acme cannot read/edit Onit's product).
  (2) Inheritance resolution: with no overrides, ResolvedProfile.brand_voice == org's;
      with `brand_voice_override` set, ResolvedProfile.brand_voice == the override;
      provenance per field is correct (`org` | `product_override`).
  (3) Agent uses the resolver: a content_engine run with product_id reflects the product's
      positioning + value_props + product_competitors in the draft; with no product_id, it
      reflects the org-level view unchanged (no regression).
  (4) UTM behavior: content under a product uses utm_source_override if set, else org default,
      else type default; campaign slug is prefixed with the product slug.
  (5) Nullable product_id everywhere: an existing org-level run/artifact/metric_point with
      product_id=NULL behaves exactly as before (full backwards compatibility).
  (6) Dashboard scoping: with a product_id filter, the funnel view returns only that product's
      metric_points; org-level view (no filter) returns everything.

## Done looks like
- A product can be created, edited, confirmed under an org. Inheritance is real and visible
  (provenance per field).
- All existing agents work unchanged when no product is selected (backwards compat).
- With a product selected, content + briefs reflect the product's positioning, persona, value
  props, and competitors — while brand voice / banned claims inherit (or override) cleanly.
- UTM campaigns are prefixed with the product slug; the dashboard can filter to a product.
- Smoke green with the six new tests; pushed to main; uvicorn + worker restarted on the new
  schema; one seeded example product exists in the dev DB.

## Explicitly out of scope (DO NOT BUILD — these are future phases)
- Document ingestion / PMM doc upload + field extraction. Next build. (Product profile fields
  are populated manually for now.)
- Enhanced content types (buyer guide, white paper, case study, report). Future.
- The Campaign object as a first-class entity. Future.
- AI-generated distribution strategy (step 5 in the vision). Future.
- Asset library (step 6). Future.
- Roll-up dashboards across products. Future (the per-product filter is enough for now).
- Brand assets (logos, colors) on the org/product. Future (mention in vision.md if you write
  it but don't build it here).
- UI polish, navigation redesign, nested layouts. Replit will handle UI.
