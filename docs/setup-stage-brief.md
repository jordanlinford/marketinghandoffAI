# Setup stage — build brief

Hand this to Claude Code. Read CLAUDE.md first.

## Goal
Build the **Setup** stage: how an org tells the system who they are, before any campaign
runs. Setup populates a durable, org-level **OrgProfile** — ICP, product/value-prop,
competitors, brand voice, keywords, conversion goal, constraints. It can be filled three
ways (the recurring pattern): **manual** (a form), **derived** (paste a URL → crawl →
LLM drafts a profile the user confirms), or **sampled** (upload a CSV of existing customers
→ infer ICP fields). The OrgProfile becomes the input every later stage reasons from.

This is org-level config, set up once and reused. It is NOT per-campaign (campaigns come
later and will reference this).

## Data model — two objects, kept separate on purpose
Add an **`org_profiles`** table (tenant-scoped — `org_id`, one active profile per org):
- id, org_id, created_at, updated_at
- product_summary (text), value_prop (text)
- icp (JSON: industries[], min_employees, min_revenue_usd, regions[], titles[], notes)
- competitors (JSON: list of {name, url?})
- keywords (JSON: string[])
- brand_voice (text — tone/style rules), banned_claims (JSON: string[])
- conversion_goal (text — e.g. "book a demo"), conversion_event (text)
- website_url (text), crawl_summary (text — the LLM draft, kept for reference)
- source (JSON: which fields came from form vs crawl vs csv, for transparency)
- confirmed (bool — false until the user reviews/saves; drafts are never silently trusted)

Leave a clear comment that **CampaignBrief is a future, separate, per-campaign object** that
will reference org_profiles.id — do NOT build it now, just don't block it.

Mirror the table in `sql/schema.sql` (org_id + add `org_profiles` to the RLS array).
This changes the schema → on SQLite, delete the local agenthq.db and re-seed so create_all()
picks it up cleanly.

## Endpoints (all tenant-scoped via `scoped()` — the load-bearing rule)
- `GET  /api/profile` — return the org's profile (or an empty skeleton if none yet).
- `PUT  /api/profile` — create/update; sets confirmed=true. This is the user's saved truth.
- `POST /api/profile/crawl` — body {url}. Fetch + summarize (see below). Returns a DRAFT
  profile (not saved). The UI shows it for the user to edit, then they PUT to confirm.
- `POST /api/profile/from-csv` — multipart CSV of existing customers. Reuse the EXISTING
  upload/parsing logic in app/api/uploads.py / app/data_sources/csv.py (don't duplicate it).
  Infer draft ICP fields from the sample: most-common industries, median employees/revenue.
  Returns a DRAFT (not saved), same confirm-then-PUT flow.
All three "draft" endpoints return data the user reviews; only PUT writes confirmed truth.

## Website crawl (the one real external dependency — handle defensively)
New module `app/setup/crawl.py`:
- Fetch a SMALL set of pages: the given URL, plus same-domain /about, /product(s),
  /pricing, /solutions if discoverable from the homepage links. Cap at ~5 pages.
- Use httpx (already a dep) with a short timeout (~8s/page) and a normal User-Agent.
- Cap total extracted text (~30k chars) before sending to the LLM. Strip scripts/styles/nav
  boilerplate; keep visible text.
- **Fail gracefully**: timeout, non-200, bot-block, JS-only page with no server-rendered
  text, or any exception → return a clear "couldn't read the site, fill the form manually"
  result with status, NOT a 500. The form path must always work as fallback (manual mode).
- Be a polite crawler: same-domain only, no recursion beyond the homepage's links, hard cap
  on page count. Do not follow off-site links.

## LLM-drafted profile (the magic — but a DRAFT)
New module `app/setup/draft.py`:
- If ANTHROPIC_API_KEY is set, send the cleaned crawl text (or the CSV-derived summary) to
  Claude and ask it to propose: product_summary, value_prop, a draft icp (industries, size),
  likely competitors, candidate keywords, and a suggested conversion goal. Return STRUCTURED
  JSON (instruct the model to return only JSON; parse defensively, fall back on parse fail).
- If no key (or the call fails): return a minimal draft built from whatever was extractable
  (title, meta description, headings) so the flow still works — same template-fallback pattern
  as app/agents/synthesis.py. Reuse that pattern; don't invent a new one.
- The model PROPOSES; the user DISPOSES. Mark every LLM-proposed field in `source` as
  "crawl"/"llm" so the UI can show "we guessed this — confirm it." Never present a draft as
  fact. (Same honesty discipline as the CSV intent rule.)

## UI (app/static/ui.html)
Add a **Setup** view (a new top section or tab — your call; keep it simple, same style).
- Three entry paths, clearly labeled: "Fill in manually", "Crawl my website" (URL input →
  shows the draft, editable), "Upload customer sample" (CSV → shows inferred ICP draft).
- A single editable profile form covering all OrgProfile fields, pre-filled from whatever
  draft path was used, with draft-sourced fields visually marked as "suggested — confirm".
- A clear **Save profile** button (the confirm step → PUT /api/profile).
- Show whether a confirmed profile already exists; allow editing it.
- The crawl/csv drafts must be obviously NOT-yet-saved until the user hits Save.

## Constraints (from CLAUDE.md — do not drift)
- `scoped(Model, org_id)` is the ONLY way to read tenant tables. New profile endpoints included.
- Reuse existing CSV parsing (app/data_sources/csv.py) for the customer-sample path — no dupe.
- Reuse the synthesis.py LLM-or-template fallback pattern for draft.py — no new pattern.
- Don't touch the TS app (client/ server/ shared/ public/ package.json) or .replit's
  [deployment] block.
- Crawl must fail gracefully to manual mode — never 500, never hang the request.
- Keep `python -m scripts.smoke` green. ADD tests:
  (1) PUT then GET /api/profile round-trips and confirmed=true; tenant isolation holds
      (another org can't read this org's profile).
  (2) POST /api/profile/crawl with a mocked fetch (don't hit the real internet in the test)
      returns a draft with confirmed=false.
  (3) POST /api/profile/from-csv with a small in-memory customer CSV returns a draft ICP
      whose industries reflect the sample.

## Done looks like
- A user with no profile can: paste their URL → see an editable, LLM-drafted profile →
  correct it → Save → it persists and reads back confirmed.
- The manual form works with no crawl and no key (graceful fallback).
- The customer-sample CSV produces a sensible draft ICP.
- Drafts are never saved without an explicit Save; draft fields are marked "suggested".
- Tenant isolation holds; smoke green with the three new tests; committed and pushed.

## Explicitly OUT of scope (don't build, don't block)
- CampaignBrief / per-campaign anything — future stage; just reference-friendly.
- Wiring the OrgProfile INTO market_intel or any agent — that's the next build after this.
- Live API connections (ZoomInfo/Apollo) — the seam stays; not this brief.
