# Asset Library — build brief (Phase 4 / step 5)

Hand this to Claude Code. Read CLAUDE.md and the build briefs in docs/ first
(content-engine, quality-loop, dashboard, product-layer, document-ingestion, ia-restructure).

## Goal
Turn the step-5 Library placeholder into a real **Asset Library**: the organizational memory
layer. A single, filterable, searchable view over ALL of an org's marketing assets —
generated content, uploaded documents, and market briefs — each viewable, copyable,
downloadable, and (where possible today) reusable. This is mostly a READ-and-ORGANIZE build
over data that already exists; it introduces almost no new persistent state.

## Core concept: a unified Asset view over three existing sources
Do NOT create a new asset store. Instead, build a **unified read model** that surfaces three
existing kinds as a common "asset" shape:

1. **Content assets** — artifacts produced by the content engine (content_draft,
   content_idea, and any content versions). Already in `artifacts`.
2. **Document assets** — uploaded PMM/source docs. Already in `product_documents`.
3. **Brief assets** — market_intel briefs. Already in `artifacts` (or wherever briefs persist).

Define a normalized projection (a Python view model + an API response shape), e.g.:
```
Asset = {
  id, asset_kind: "content" | "document" | "brief",
  title,            # derived: content subject/topic, doc filename, brief title
  asset_type,       # email|ad|social_post|blog_outline | messaging_framework|one_pager|... | market_brief
  product_id, product_name,   # null/“Org-level” when unscoped
  status,           # draft|ready|approved|rejected | ingesting|extracted|failed | (briefs: succeeded)
  campaign,         # utm_campaign when present (content), else null
  created_at, updated_at,
  cost_usd,         # for generated assets, when known
  source_ref,       # pointer back to the underlying row so detail view can load native shape
  grade,            # content grade overall when present
}
```
The library lists/filters/sorts over this projection. Detail view loads the NATIVE underlying
object (so content shows its blocks + grade + UTMs, a doc shows extracted text + insights,
a brief shows its full structure). Don't flatten away the rich detail — normalize the CARD,
preserve the native DETAIL.

## Filtering / sorting / search (this is what makes it "memory", not a list)
Filters (all optional, combinable; all tenant + scoped):
- **product** (incl. "Org-level / none")
- **asset_kind** (content / document / brief)
- **asset_type** (email, ad, ..., messaging_framework, market_brief, ...)
- **status**
- **campaign** (utm_campaign)
- **date range** (created_at)
Sort: newest first (default), oldest, by grade (content), by cost.
Search: a text match over title + (for content) body text + (for docs) extracted_text +
(for briefs) brief text. Keep it a simple LIKE/contains across the projection's searchable
fields — no vector search, no new dependency.

Provide `GET /api/assets` with query params for the filters/sort/search above, returning the
normalized Asset list, scoped via scoped() across all three underlying tables. Paginate
(limit/offset) — an org will accumulate many assets.

## Per-asset actions
For each asset, support:
- **View** — opens detail in its native shape (content blocks/grade/UTMs; doc text/insights;
  brief sections).
- **Copy to clipboard** — copies the asset's primary text. For multi-block content, compose
  the blocks into clean plain text (subject + body + cta, etc.). For docs, copy extracted
  text. For briefs, copy the rendered brief.
- **Download as file** — single asset:
    * content → .md (composed from blocks) or .json (raw structured) — offer both.
    * brief → .md.
    * document → the ORIGINAL uploaded file (re-served from storage), plus a .txt of the
      extracted text.
  Single-asset only in v1. (No bulk/zip export yet.)
- **Reuse — two tiers:**
    * **Available today:** "Regenerate from this" / "Use as basis for new content" — routes into
      the content engine pre-filled with this asset's product + topic/positioning as the
      starting point (for content + brief assets). "Duplicate" for content assets (creates an
      editable copy as a new draft).
    * **Wired for tomorrow:** "Add to campaign" — present on content + document + brief assets,
      but since Campaign Builder (step 4) is still a placeholder, this routes to the step-4
      placeholder page with the asset id carried in the URL/state (so when Campaign Builder
      ships, the wire is already there). Label it clearly; do not fake campaign functionality.

## Scoping & the header product selector
- Respects the global product selector from Build A: when a product is selected, the library
  defaults its product filter to that product (user can clear it to see all). When none is
  selected, shows org-wide.
- Everything tenant-isolated via scoped() across all three source tables. Cross-org assets
  never appear; cross-org detail/download/reuse denied.

## Constraints (from CLAUDE.md)
- This is primarily a READ model. The only persistent additions allowed, if needed:
  - "Duplicate" creates a new artifact (reuses existing content artifact creation).
  - Nothing else should write. No new tables for the library itself — it's a projection.
- `scoped(Model, org_id)` for ALL reads/writes across artifacts, product_documents, briefs.
  Cross-org must fail closed (tested).
- Reuse the existing content-engine entry points for "regenerate from this"/"duplicate" —
  don't reinvent generation.
- Document download re-serves the original file from the tenant-scoped storage path created in
  the document-ingestion build — do NOT expose raw filesystem paths; serve through a scoped
  endpoint that checks org ownership.
- No new agents. No new dependencies. No LLM calls in the library itself (it's organize/serve).
- Don't touch the TS app or .replit's [deployment] block.
- Schema: ideally zero. If a thin helper column is unavoidable, mirror in sql/schema.sql + RLS;
  prefer doing it as a query-time projection with no schema change.
- Keep `python -m scripts.smoke` green. ADD tests (hermetic):
  (1) `GET /api/assets` returns content + document + brief assets normalized into the common
      shape for an org; counts match the underlying rows.
  (2) Filters work: by product, by asset_kind, by asset_type, by status, by campaign, by date
      range; combined filters AND correctly; search matches title/body/extracted_text.
  (3) Tenant isolation: Acme's `GET /api/assets` returns zero of Onit's assets; cross-org
      detail/download/reuse denied (403/404).
  (4) Download composition: a multi-block content asset composes into clean .md; a document
      download is scoped (cross-org denied) and returns the right file.
  (5) Reuse routing: "regenerate from this" creates a new content run pre-filled from the
      source asset's product/topic; "duplicate" creates a new draft artifact preserving blocks;
      "add to campaign" returns the step-4 placeholder target with the asset id (no campaign
      created — placeholder).
  (6) Product-selector scoping: with a product selected, the default asset list is filtered to
      that product; clearing shows org-wide.

## UI (replace the step-5 placeholder; minimal, functional, polish deferred to Replit)
- Replace the Library placeholder with a real view:
  - **Filter bar** (now ACTIVE): product, kind, type, status, campaign, date range, search box.
  - **Asset grid/list**: cards showing title, kind badge, type, product, status, date, grade
    (content), cost (when known). Click → detail.
  - **Detail panel/drawer**: native rendering per kind (content blocks + grade + UTMs + tagged
    URL; document extracted text + accepted insights; brief sections). Action buttons: View,
    Copy, Download (with format choice for content), Regenerate-from-this, Duplicate,
    Add-to-campaign (routes to step-4 placeholder).
  - Empty state: "No assets yet — generate content in Create, or upload a framework in
    Products, and everything will collect here."
- No styling investment beyond expressing the structure. Replit handles polish.

## Done looks like
- The Library lists every content draft, uploaded document, and market brief for the org in a
  unified, filterable, searchable view.
- Filtering by product/kind/type/status/campaign/date and text search all work and combine.
- Any asset can be viewed in its native detail, copied, and downloaded (content as md/json,
  docs as original + extracted text, briefs as md).
- "Regenerate from this" and "Duplicate" work today; "Add to campaign" is present and wired to
  the step-4 placeholder, ready for Campaign Builder.
- Respects the product selector; fully tenant-isolated. Smoke green with the six new tests;
  pushed; uvicorn restarted (worker only if a schema change was unavoidable).

## Explicitly out of scope (do NOT build)
- Bulk / zip / multi-asset export. Single-asset only in v1.
- A new asset storage table — the library is a projection over existing data.
- Campaign functionality — "Add to campaign" only routes to the placeholder for now.
- Presentation/slide export ("download as deck") — future; note it in the vision but don't build.
- Versioned asset diffing UI beyond what content versions already provide.
- Any LLM calls inside the library itself.
- Styling polish / component redesign — Replit's job.
