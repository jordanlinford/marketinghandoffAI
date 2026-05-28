# Campaign Builder — build brief (Phase 3 / step 4)

Hand this to Claude Code. Read CLAUDE.md and ALL build briefs in docs/ first (content-engine,
quality-loop, dashboard, product-layer, document-ingestion, ia-restructure, asset-library).

## Goal
Turn the step-4 Campaign Builder placeholder into a real **orchestration layer**. A campaign is
a first-class operational object that COORDINATES marketing execution: the user picks a parent
asset + CTA + audience + objective + channels; the system PROPOSES a campaign plan (derivative
assets + channel mix + lightweight cadence); the user approves/modifies the plan; the system
then GENERATES the approved derivative assets — via the existing content engine — into the
approval queue and library, all tagged to one coordinated campaign UTM scheme.

The feeling target: **"build and launch a coordinated marketing motion,"** an AI campaign
strategist + orchestration planner — NOT a mass content generator.

## THE LOAD-BEARING ARCHITECTURAL PRINCIPLE (do not violate)
**Content and campaigns are SEPARATE concepts. Campaigns REFERENCE assets; they do NOT own
content-generation logic.**
- Content generation stays entirely in the existing content engine. Campaign Builder calls it;
  it does not reimplement, fork, or embed generation logic.
- A campaign holds references (asset ids) + coordination state. Assets can exist independently
  of any campaign (as they do today). A campaign is a coordination layer over assets.
- This separation is what keeps the system clean as it grows. Hold it strictly.

## The campaign object (new first-class entity: `campaigns`)
New table `campaigns`, tenant-scoped (org_id) with optional product_id (Build A pattern):

Core metadata:
- `id`, `org_id`, `product_id` (nullable — campaigns can be org-level or product-scoped)
- `name`, `description`
- `campaign_type` (enum, light: awareness | demand_gen | launch | nurture | competitive | other)
- `objective` (text — what success looks like)
- `status` (enum: draft | planned | generating | active | complete | archived)
- `owner` (the dev-auth user email, for now)
- `start_date`, `end_date` (nullable)
- `parent_asset_id` (nullable FK — the anchor asset the campaign drives toward, e.g. a blog/
  white paper/report; the destination)
- `primary_cta` (text — e.g. "visit blog", "download report", "book demo")

Targeting:
- `target_personas` (JSON list)
- `target_segments` (JSON list)
- `target_industries` (JSON list, optional)
- `target_account_ref` (JSON, nullable — future-ready, lightweight placeholder in v1)

Channels:
- `selected_channels` (JSON list — e.g. linkedin, google, email, meta, reddit, webinar)
- `channel_recommendations` (JSON — the AI's recommended mix + rationale)
- `channel_notes` (JSON, optional)

Execution state:
- `plan` (JSON — the approved proposal: the list of derivative assets to generate, with
  per-item channel/type/angle/cadence)
- `generated_asset_ids` (JSON list — artifacts produced for this campaign; the reference, not
  ownership)
- `utm_campaign` (the coordinated campaign slug all child assets share — product-prefixed per
  Build A convention)

Performance hooks (RESERVE the columns; do NOT build logic on them in v1):
- `kpis` (JSON, nullable)
- `linked_metric_point_query` (JSON/text, nullable — how this campaign's UTMs map to
  metric_points for future attribution)
Mirror in sql/schema.sql with RLS.

NOTE on referencing assets: store campaign↔asset links via `parent_asset_id` and
`generated_asset_ids`. Do NOT add a campaign_id column that makes artifacts "belong" to a
campaign in a way that breaks their independence — a nullable `campaign_id` on artifacts is
acceptable as a convenience back-reference, but the artifact remains a first-class asset that
exists with or without the campaign. (Library already treats them as independent.)

## The workflow (propose-first — the heart of this build)

### Step 1 — Define (user input)
A form: pick a **parent asset** (from the library — content, or later a report/white paper),
a **primary CTA**, **audience** (personas/segments, defaulting from the product profile),
**objective**, **campaign type**, and **candidate channels**. Product selector scopes it.

### Step 2 — Propose (one LLM call → a plan, NOT content)
The system makes ONE LLM call (grounded in: the resolved product profile, the parent asset, the
CTA, audience, objective, channel best-practices) that returns a structured **campaign plan**:
- A recommended **set of derivative assets** to create, each as
  `{content_type, channel, angle/topic, rationale, cadence_hint}`.
  Example output: 3 LinkedIn posts, 2 nurture emails, 2 retargeting ads, 1 carousel (structure
  only — see note), 1 sales follow-up email.
- A recommended **channel mix** with rationale (generic best-practice for v1 — see below).
- **Lightweight cadence/sequencing** guidance (e.g. "post LinkedIn assets over 5 business days,
  support with retargeting ads") — high-level, not a scheduling engine.
This is a PLAN, cheap to produce. No content is generated yet. Use synthesis.py's fallback
pattern; on no key, produce a deterministic rule-based plan from campaign_type + CTA + channels.

### Step 3 — Review & modify (human gate)
The user sees the proposed plan and can: add/remove derivative items, change a channel or
type per item, edit the cadence, adjust the channel mix. This is the strategic control point —
the campaign feels like a plan you shape, not output dumped on you. The approved plan is saved
to `campaign.plan` and status → `planned`.

### Step 4 — Generate (on approval → calls the content engine)
On "Generate campaign," for each approved derivative item, the system **calls the existing
content engine** (not a reimplementation) to produce that asset, grounded in the product
profile + the parent asset + the item's angle, tagged with the coordinated campaign UTM scheme
(shared utm_campaign; per-item utm_source=channel, utm_medium=type, utm_content=item slug).
- Each generated asset flows through the existing guardrail + review-gate path (content_review_
  mode still governs). Generated assets land in the approval queue and the library, with
  `campaign_id` set as a back-reference and `generated_asset_ids` updated on the campaign.
- Status → `generating` during, → `active` when the batch completes.
- Capture per-asset cost; surface total campaign cost.
- This step is the only expensive part, and it only runs after human approval of the plan.

## Channel recommendations — generic now, hooks for grounding later
- v1: generalized best-practice recommendations informed by campaign_type, persona, funnel
  stage (inferred from CTA/objective), CTA type, and content type. Deterministic-friendly so it
  works without an LLM.
- RESERVE hooks (don't implement): the recommendation function should take an optional
  `performance_context` argument (default None) that, in a FUTURE build, will be populated from
  the substrate (metric_points: channel conversion data, attribution outcomes, engagement
  patterns). v1 passes None and uses best-practice. Document the seam clearly.

## Distribution strategy — lightweight only (full engine deferred)
- FOLD IN: the proposal includes recommended channels, basic sequencing, cadence suggestions,
  and high-level deployment guidance (e.g. "post LinkedIn assets over 5 business days, support
  with retargeting").
- DO NOT BUILD: timing optimization, retargeting orchestration, cross-channel sequencing
  engine, sales coordination workflows, budget allocation, adaptive optimization, scheduling/
  publishing automation. Those are a future dedicated Strategy Layer build. Keep the campaign's
  cadence guidance textual/advisory, not an execution engine.

## Carousels / visual derivatives
If the plan proposes a carousel/infographic/visual asset, generate its STRUCTURE only (the
per-slide copy / structured content) using the existing block-based content shape — do NOT
build a visual renderer. Mark it as structure-only with a note "visual rendering is a future
layer." (Same discipline already established for the content object.)

## API
- `POST /api/campaigns` — create (draft) with the Step-1 inputs. Scoped.
- `GET /api/campaigns` — list for org (scoped), filter by product/status.
- `GET /api/campaigns/{id}` — detail incl. plan + generated asset refs.
- `PATCH /api/campaigns/{id}` — edit metadata, targeting, channels, and the plan (Step 3).
- `POST /api/campaigns/{id}/propose` — runs the Step-2 proposal (one LLM call), stores the
  plan, returns it. Scoped.
- `POST /api/campaigns/{id}/generate` — Step 4: generate approved derivatives via the content
  engine into the queue/library; updates generated_asset_ids + status. Scoped.
- `DELETE /api/campaigns/{id}` — archive (soft) per convention; generated assets remain
  independent in the library (do not cascade-delete content).
- The library's existing "Add to campaign" placeholder route should now actually attach an
  asset as a campaign's parent_asset (or to an existing campaign) — wire the real behavior.

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` for ALL reads/writes (campaigns + the artifact back-references).
  Cross-org must fail closed (tested).
- Campaign Builder CALLS the content engine for generation — it must not duplicate generation
  logic. Reuse the existing agent/worker path; generated assets go through the existing
  guardrail + review gate.
- Proposal + any LLM use follows synthesis.py's fallback pattern; deterministic plan when no key.
- The worker handles generation jobs as it does today (queued); the propose call can be
  synchronous (one cheap call) or queued — pick the simpler that keeps UI responsive.
- Reserve substrate + attribution hooks (performance_context arg, kpis, linked_metric_point_
  query) WITHOUT implementing them. The dimensions-column discipline from Build B applies:
  build the seam, don't branch on it.
- Schema change (campaigns table + nullable campaign_id back-ref on artifacts). Mirror in
  sql/schema.sql + RLS; delete agenthq.db + re-seed; restart uvicorn AND worker. Smoke uses its
  own DB.
- Seed: one example campaign under Onit/SimpleLegal CLM with a small approved plan + a couple
  of generated_asset_ids, so the dev DB demonstrates the shape without manual setup.
- Don't touch the TS app or .replit's [deployment] block.
- Keep `python -m scripts.smoke` green. ADD tests (hermetic — stub the LLM):
  (1) Create a campaign (draft) with Step-1 inputs; fields persist; tenant-isolated.
  (2) Propose: stubbed LLM returns a structured plan; plan persists on the campaign; with no
      key, a deterministic rule-based plan is produced (non-empty, sensible for the type).
  (3) Plan edit: PATCH modifies the plan (add/remove derivative items); status → planned.
  (4) Generate: for an approved 3-item plan, generation calls the content engine 3×, creates 3
      artifacts tagged with the SHARED utm_campaign and per-item source/medium/content; assets
      land with campaign_id back-ref; generated_asset_ids updated; assets also appear via the
      library projection. Confirms campaigns REFERENCE (don't own) — the artifacts are normal
      content artifacts.
  (5) Separation: deleting/archiving a campaign does NOT delete its generated assets (they
      remain in the library); an asset can exist with campaign_id NULL (independence holds).
  (6) Channel rec hook: the recommendation function accepts performance_context=None and returns
      best-practice; assert it's callable WITH a context arg (seam exists) but v1 passes None.
  (7) Tenant isolation: Acme cannot read/propose/generate/patch Onit's campaigns (403/404);
      cross-org parent_asset attach denied.
  (8) Review gate honored: generated campaign assets respect content_review_mode (gate_all →
      land in approval queue as proposals; guardrail/all_through as today).

## UI (replace the step-4 placeholder; minimal, functional, polish deferred to Replit)
- **Campaign list**: existing campaigns with name, type, status, product, asset count, created.
  "+ New campaign" entry.
- **Create/Define view (Step 1)**: form for the inputs above; parent asset picker pulls from the
  library (filtered to the current product when selected); channel multi-select.
- **Proposal view (Step 2/3)**: render the proposed plan — derivative asset list (type, channel,
  angle, rationale, cadence), recommended channel mix with rationale, cadence guidance. Each
  item editable/removable; add-item control; "Approve plan" and then "Generate campaign."
- **Campaign detail (Step 4+)**: status, the plan, the generated assets (linking into the
  library/their detail), total cost, the shared UTM scheme, and the reserved (empty) KPI/
  performance section labeled "Performance tracking — connects to Insights once reports are
  uploaded" (hook visible, not built).
- Wire the library's "Add to campaign": selecting it attaches the asset as a campaign parent (or
  to an existing campaign) — real behavior now, replacing the placeholder banner.
- No styling investment beyond expressing structure. Replit handles polish.

## Done looks like
- A user picks a parent asset (e.g. a blog) + CTA ("visit blog") + audience + objective +
  channels → clicks Propose → sees a coordinated plan (e.g. 3 LinkedIn posts, 2 emails, 2 ads,
  1 carousel structure, 1 sales email) with channel mix + cadence guidance.
- The user edits the plan, approves it, clicks Generate → the content engine produces those
  assets, tagged to one shared campaign UTM scheme, through the existing review gate, landing
  in the approval queue + library, back-referenced to the campaign.
- The campaign object holds the coordination state; the assets are independent library citizens
  that happen to reference the campaign. Archiving the campaign leaves the assets intact.
- Channel recs are best-practice with a reserved performance_context seam; KPI/attribution
  hooks are present but unbuilt. Smoke green with the eight new tests; pushed; uvicorn + worker
  restarted; one seeded example campaign exists.

## Explicitly out of scope (DO NOT build — future layers)
- Full distribution/strategy engine: timing optimization, retargeting orchestration,
  cross-channel sequencing, sales coordination, budget allocation, adaptive optimization.
- Scheduling / publishing automation (no auto-posting anywhere — consistent with the whole
  system's "no autonomous publishing" stance).
- Advanced/multi-touch attribution and budget analytics (only reserve the hooks).
- Visual rendering of carousels/infographics (structure only).
- Substrate-grounded channel optimization (reserve the performance_context seam; don't wire it).
- Campaign templates/cloning, multi-campaign roll-up dashboards (future).
- Styling polish — Replit's job.
