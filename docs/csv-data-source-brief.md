# CSV data source — build brief

Hand this to Claude Code. Read CLAUDE.md first.

## Goal
Make the market brief TRUE for the user's real accounts. Today `market_intel` runs on
`StubMarketDataSource` (fake companies). Add a CSV upload path: the user uploads a list of
their real target accounts, and the agent sizes + fit-scores THOSE accounts against the
org's ICP — flowing through the SAME `market_intel` agent, untouched. This proves the
"swap a real source behind the `MarketDataSource` seam" pattern (the ZoomInfo/Apollo swap
later is then the same shape + an API call).

## The seam (do not bypass)
`app/data_sources/base.py` defines `MarketDataSource` with `find_companies(icp, limit)` and
`keyword_universe(seeds, limit)`. Add a new implementation `CsvMarketDataSource`. The
`market_intel` agent MUST NOT change — it already calls `ctx.get_market_data()`. The only
change to selection logic is in `app/worker.py::_resolve_market_data`: if the run is bound
to an uploaded list, return `CsvMarketDataSource(that file)`; otherwise return the stub as
today.

## Data model
Add an `uploads` table (tenant-scoped — `org_id` on it like every other table):
- id, org_id, filename, uploaded_by (user id), row_count, created_at
- store the parsed rows as JSON on the row, OR the raw CSV text — pick the simpler one and
  note which. (Team-scale data; no blob store needed.)
Add a nullable `upload_id` to `runs` (which list this run analyzed; null = stub, preserving
current behavior). Persisting this also fixes the latent "task not threaded to the run" gap
— thread `upload_id` from trigger → run → worker → context.

## Upload endpoint
`POST /api/uploads` (multipart file). Parse the CSV server-side. Be forgiving about headers:
accept common column names case-insensitively — company/name, employees/headcount/size,
revenue, industry/sector. Map to the fields `CompanyRecord` needs
(name, employees, revenue_usd, industry). Missing numeric fields → 0 or None, don't crash.
Persist an `uploads` row. Return its id + a small preview (row_count, first few mapped rows)
so the UI can confirm the mapping looked right.
`GET /api/uploads` lists the org's uploads (id, filename, row_count, created_at).
BOTH endpoints read/write tenant tables ONLY via `scoped(Model, org_id)` — never a bare
`select()`. This is the load-bearing rule.

## CsvMarketDataSource
Implements `MarketDataSource`:
- `find_companies(icp, limit)`: load the upload's rows, map to `CompanyRecord`, and compute
  fit_score from the ICP criteria (NOT random — this is the real native fit-scoring):
  reward industry ∈ icp.industries, employees ≥ icp.min_employees, etc. Document the simple
  scoring formula in a comment. Set `intent_score = 0.0` and `source = "csv"` — be HONEST
  that a static upload carries no live intent signal (intent needs a feed like ZoomInfo;
  do not fake it).
- `keyword_universe(seeds, limit)`: a CSV has no keyword data. Return [] (the agent already
  handles empty clusters), OR delegate to the stub's keyword logic and label it clearly.
  Pick one; note the choice. Do NOT invent keyword volumes from nothing.

## Trigger + wiring
`POST /api/runs` accepts an optional `upload_id`. Persist it on the `Run`. The worker reads
it: present → `CsvMarketDataSource(upload)`, absent → `StubMarketDataSource` (unchanged).
Validate the `upload_id` belongs to the caller's org (via `scoped`) before use — never
trust a client-supplied id without the tenant check.

## UI (app/static/ui.html)
In the Runs panel: an "Upload account list" control (file picker → `POST /api/uploads`),
a small dropdown of existing uploads from `GET /api/uploads`, and when a list is selected,
"Run market intelligence" sends that `upload_id`. Default (no selection) = stub, exactly as
today. After upload, show the row-count + preview so the user trusts the column mapping.
Keep the honesty: when a run used a CSV, the brief/citation should reflect "intent
unavailable for uploaded lists" rather than showing fake intent numbers.

## Constraints (from CLAUDE.md — do not drift)
- `scoped(Model, org_id)` is the ONLY way to read tenant tables. New `uploads` endpoints
  included.
- The `market_intel` agent does not change. Data flows through `ctx.get_market_data()`.
- Don't touch the TS app (`client/ server/ shared/ public/ package.json`) or `.replit`'s
  `[deployment]` block.
- Keep `python -m scripts.smoke` green. ADD tests: (1) upload a small in-memory CSV →
  `find_companies` returns mapped+fit-scored records with `source == "csv"`; (2) a run bound
  to an `upload_id` produces a brief from the uploaded accounts; (3) tenant isolation still
  holds — another org cannot read or run against this org's upload.

## Done looks like
- `POST /api/uploads` ingests a CSV, returns id + preview; `GET /api/uploads` lists them.
- Triggering a run with `upload_id` produces a brief sized/scored from the UPLOADED
  accounts (visible in the UI), with intent honestly marked unavailable.
- Triggering with no upload still uses the stub (no regression).
- Fit scores reflect the ICP criteria, not randomness.
- `smoke` green with the three new tests; committed and pushed.
