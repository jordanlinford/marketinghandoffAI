# UI / IA restructure — build brief (Phase 1.5)

Hand this to Claude Code. Read CLAUDE.md and the existing build briefs in docs/ first
(content-engine, quality-loop, dashboard, product-layer, document-ingestion). The point of
this brief is to make the *information architecture* match the workflow those builds
collectively enable — without changing any of their functionality.

## What this build is — and isn't

This is **pure information architecture work.** No new features, no new agents, no schema
changes, no new dependencies, no styling polish. The job is to **relocate existing
functionality into a workflow-shaped IA** and add a landing screen that orients the user.

Specifically:
- DO restructure the top-level navigation into the six-step spine below.
- DO add a Command Center as the new landing screen with the live elements listed.
- DO move existing functionality into its correct step (e.g. Documents + the review queue
  live INSIDE Product/Feature Setup, not as a separate page).
- DO add real but empty placeholder screens for Campaign Builder and Asset Library, with the
  intended workflow visible so users see where future capability will land.
- DO NOT add new agents, endpoints, models, or smoke tests.
- DO NOT change the visual style beyond what's required to express the new structure.
- DO NOT remove or break any current functionality — every existing capability must remain
  reachable through the new IA.
- DO NOT touch the TS app or .replit's [deployment] block.

The principle: **the app structure should be the workflow itself.** A marketing leader opens
this for the first time and the IA tells them what to do next.

## The six-step workflow spine (top-level navigation)

Replace the current flat tab structure with these six top-level destinations, in this exact
order. Use clear step names — *not* abbreviations:

1. **Organization** — Org Setup. Foundational company intelligence.
2. **Products** — Product / Feature Setup. PMM intelligence layer.
3. **Create** — Content creation.
4. **Campaigns** — Campaign Builder. (Placeholder for now; see below.)
5. **Library** — Asset Library. (Placeholder for now; see below.)
6. **Insights** — Dashboard, performance, and recommendations.

The Command Center (next section) is the home — reachable via a "Home" / logo click and the
default landing route. The six steps appear as the primary nav alongside it.

## The Command Center (the new landing screen)

Default route after sign-in. The feeling target: *"the AI marketing operating system is
guiding me through execution"* — not *"here are tabs and charts."*

The screen answers one question: **"What should I work on next?"** Compose it from the
existing data already in the system. No new endpoints required — read from existing scoped
queries.

Sections (rendered as cards or rows; use whatever existing layout pattern is cleanest):

- **Active products/features** — list of confirmed ProductProfiles for the org, with a
  one-line summary (e.g. "3 value props, last extraction 2 days ago"). Click → step 2 for
  that product.
- **Pending review items** — count of extracted_insights with status=pending across all
  products. Click → step 2 → review queue for the relevant product.
- **Content awaiting approval** — count of proposals in the approval queue (action_type
  content_review). Click → that proposal in step 3.
- **Recent runs** — last 5 runs with status, agent_key, age, cost. Click → run detail.
- **Recent performance alerts** — last 3 suggestions from the dashboard's suggestions table.
  Click → step 6 with that suggestion highlighted.
- **Recommended next actions** — a small, deterministic, rule-based list. Examples (rules,
  not LLM):
    * If any product has 0 documents → "Upload a PMM framework for {product} to unlock
      grounded content."
    * If any product has pending review_count > 0 → "{N} extracted insights waiting for
      review on {product}."
    * If a run failed in the last hour → "A {agent} run failed — check the logs."
    * If a content_engine run produced drafts but no associated metric_points exist for the
      tagged campaign → "Drafts produced for campaign {x}, but no performance data uploaded
      yet — publish using the tagged links or upload a report."
  Same evidence-backed pattern as the dashboard's suggestions (each carries the data that
  motivates it). LLM-free; uses existing queryable state.

The header product selector REMAINS — Command Center is org-level by default, but if a
product is selected, sections scope to that product (e.g. pending review items, drafts
awaiting approval, runs filtered to that product). Same selector pattern Build A established.

## How existing functionality maps into the new spine

This is the relocation matrix. Functionality stays the same; *where it lives* changes.

### Step 1 — Organization
What lives here (today, the current "Setup" tab's org section):
- Org profile form (name, website, brand voice, banned claims, conversion goal, ICP, etc.).
- The three intake paths: manual / crawl / customer CSV (the additive Setup merge).
- Org-level rubric (`content_rubric`).
- `content_review_mode` selector (the gate setting).

What this build adds as **labeled-empty sections** (so the structure is visible, no new
fields): "Design system & brand assets — coming soon." A single sub-heading is enough; do
not build the upload control yet.

### Step 2 — Products / Feature Setup
This is where the PMM intelligence work lives. Per product:
- Product profile form (positioning, target_persona, value_props, proof_points,
  differentiators, key_features, use_cases, product_competitors). Already exists.
- Inheritable overrides section (brand_voice_override, banned_claims_override, etc.) — exists.
- **Documents** section (upload PMM framework docs) — currently in Setup; relocate here under
  each product.
- **Review queue** for pending extracted_insights — currently in Setup; relocate here under
  each product.
- **Field history** view (toggle per field, already wired) — surface it more prominently here.

The product index page (when no product is selected) lists products with: name, slug, status,
document count, pending-review count. "+ New product" entry point at the top.

Tone in this section should be **"teach the system about the product,"** not "manage files."
Make the upload control prominent — this is one of the core value moments. Add a small
helper line near the upload: *"Upload your PMM framework — the system extracts positioning,
value props, personas, and competitive framing for you to review."*

### Step 3 — Create Content
The existing Content tab. Two visible groupings, both functional today inside one engine:
- **Standard content**: email, ad, social_post, blog_outline. (Already built.)
- **Enhanced content**: buyer guide, white paper, case study, report. **Coming soon — show
  the labels and a disabled state, with a short note: "Enhanced content uses your product's
  full PMM framework and is generated as templated long-form output. Coming next."**

Existing behavior preserved: suggest-ideas from the latest brief, generate, grade, "give me
something better," approval queue (now reached via Command Center or via this page).

### Step 4 — Campaign Builder (PLACEHOLDER — do not build)
This step does not exist as functionality yet. Build a **real placeholder page** that shows
the intended workflow so users see the shape of what's coming. Sections (all visually present
but inactive):
- "Start with an asset" — picker (disabled, with a note: "Pick a published or approved asset
  to build a campaign around.")
- "Define the CTA" — text input (disabled).
- "Channels" — multi-select (disabled, showing common channels: LinkedIn, Google Ads, Email,
  Meta, Reddit, Webinar).
- "Audience" — segment selector (disabled).
- "Campaign objective" — dropdown (disabled).
- "We will generate" — preview labels of what the campaign will spawn:
  derivative assets, channel-specific copy variations, distribution recommendations,
  tracking structure (UTMs).

Add a clear banner at the top of the page: **"Campaign Builder — coming next. This is where
content becomes execution."** Don't make the placeholder feel like a 404 — make it feel like
a feature whose machinery is visible but inactive.

### Step 5 — Asset Library (PLACEHOLDER — do not build)
Same approach: real placeholder, intended structure visible. Show:
- Filter bar with the dimensions: org, product, campaign, asset type, funnel stage, status.
  (All disabled.)
- A grid or list area showing "Your generated and uploaded assets will live here."
- A small note: *"Library — coming next. Everything generated or uploaded will be searchable,
  filterable, and exportable from here."*

### Step 6 — Insights (Dashboard)
The existing Dashboard tab moves here. No internal restructuring needed. Rename the top-level
nav label from "Dashboard" to "Insights" to match the workflow framing — *"this is the
optimization layer, not just reporting."* All existing dashboard functionality (funnel,
production lane, suggestions, baseline + ongoing ingest, per-product filter) is preserved
unchanged.

## Required UX details

- **The header product selector remains** at the global top, as Build A established. It
  scopes Steps 2, 3, 4, 5, 6 and the Command Center.
- **Breadcrumbs**: when inside a product's detail (step 2), show "Products / {Product name} /
  {sub-section}". Apply the same pattern in Insights when scoped to a product.
- **Step numbering**: show the step numbers in the nav (1. Organization, 2. Products, ...) to
  reinforce the workflow.
- **Preserve all current URLs / API paths.** Do not break existing endpoints; only the
  HTML/JS routing changes.
- The current single `app/static/ui.html` may split into per-step partials or stay as one
  file with sections shown/hidden by route — pick the simpler approach. Keep all logic in
  the existing file unless splitting is clearly cleaner.

## Constraints

- **No new agents, models, endpoints, or schema changes.** This is layout and routing only.
- **No new smoke tests required, and existing smoke must remain green** (46 checks). If
  anything in the restructure incidentally breaks an existing test, that's a regression —
  fix it.
- **No new dependencies.**
- **No styling polish beyond what's needed to express structure.** Keep the existing visual
  system; just lay it out in the new spine. Polish is for Replit later.
- Don't touch the TS app or .replit's [deployment] block.
- Keep the change small in terms of behavior — if a button used to do X, it still does X,
  just in a new location.

## Done looks like

- The default landing screen after sign-in is the Command Center, showing the live sections
  above, populated from current scoped state. Empty states are gracefully handled (e.g.
  "No products yet — start with Organization, then add a Product to begin").
- Top nav reads: 1. Organization · 2. Products · 3. Create · 4. Campaigns · 5. Library ·
  6. Insights — with the Command Center reachable from a Home/logo click.
- Step 2 (Products) is where Documents + review queue live, under each product. Org-level
  Setup (step 1) no longer carries product-specific UI.
- Steps 4 and 5 are visible, navigable placeholder pages showing their intended workflow,
  clearly labeled as coming next — not 404s, not hidden tabs.
- Step 6 is the existing Dashboard, relabeled "Insights."
- All previous functionality is reachable via the new IA — content generation, approval
  queue, run detail, dashboard upload, suggestions, etc.
- Smoke is green (46/46) — no behavior changed, no tests should need updating.
- Pushed to main.

## Explicitly out of scope (do NOT build in this brief)

- The Campaign Builder feature itself (Phase 3). Only the placeholder page.
- The Asset Library feature itself (Phase 4). Only the placeholder page.
- Enhanced content generation (buyer guide / white paper / case study / report). Only the
  visible-but-disabled labels in step 3.
- Brand assets / design system upload in Step 1. Only the "coming soon" label.
- Any styling overhaul, theme work, or component library migration. Replit will do polish.
- Any change to agents, models, schemas, or smoke tests.
