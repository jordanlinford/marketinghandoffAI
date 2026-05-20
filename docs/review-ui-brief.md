# Review UI — P1 build brief

Hand this to Claude Code. Read CLAUDE.md first.

## Goal
The screen the team opens every morning. v1 does three things: trigger runs, read the
cited market brief, and show an approval-queue shell (empty until P2, but built
forward-ready). Internal tool — clean, fast, scannable. The approval queue is the hero
surface even while it's empty.

## Where it lives
Served by the Python/FastAPI app, separate from the TS app. Add a single static page
(one HTML file; vanilla JS or React via CDN — NO separate build step / no npm) and serve
it at `GET /ui` (StaticFiles mount or a route returning the file). Do NOT touch
`client/`, `server/`, `shared/`, `public/`, or `package.json`.

## Auth (dev vs prod)
- Dev: the page holds the current user email (default `jordan@onit.com`, editable via a
  small field) and sends it as the `X-Dev-User-Email` header on EVERY fetch.
- Prod: replace with the SSO session cookie. Leave a clear `TODO(prod)` comment. No new
  backend auth code.

## Screens
1. **Identity bar** — current user + org from `GET /api/me`; a dev email switcher.
2. **Runs**
   - "Run market intelligence" button → `POST /api/runs {"agent_key":"market_intel"}`.
   - Runs list from `GET /api/runs`: agent, color-coded status badge
     (queued/running/succeeded/failed), created time, cost. Poll every ~3s while any run
     is queued/running so status flips to succeeded live (this exercises the real worker).
   - Click a run → detail.
3. **Run detail / brief viewer** — `GET /api/runs/{id}`
   - Render `artifact.body.narrative` as markdown.
   - Render `artifact.body.structured`: sizing (TAM/SAM/SOM); `top_targets` as a table
     (name, industry, employees, fit %, intent %); `keyword_clusters` grouped by intent;
     `recommendations` as a list.
   - Show `citations`. Show run `logs` (collapsible).
4. **Approval queue** — `GET /api/review/proposals?status=pending`
   - One card per proposal: action_type, reasoning, guardrail_status badge
     (passed/blocked/pending), formatted payload, created time.
   - Approve / Reject buttons → `POST /api/review/proposals/{id}/approve|reject`
     (optional note in body).
   - Empty state: "No actions awaiting review" — expected in P0 since market_intel is
     read-only. Build it so P2 proposals render with no rework.

## Endpoints (all already exist — bind, don't rebuild)
`GET /api/me` · `GET /api/agents` · `POST /api/runs` · `GET /api/runs` ·
`GET /api/runs/{id}` · `GET /api/review/proposals` ·
`POST /api/review/proposals/{id}/approve` · `POST /api/review/proposals/{id}/reject`

## Constraints (from CLAUDE.md — do not drift)
- Don't touch the TS app or the `.replit` `[deployment]` block.
- If you add ANY new backend endpoint, it reads tenant tables only via
  `scoped(Model, org_id)` — never a bare `select()`. The UI calls existing endpoints,
  which already enforce this; prefer adding zero new DB-touching endpoints.
- Keep `python -m scripts.smoke` green. Add ONE test: the `/ui` page returns 200 and the
  existing API + tenant-isolation tests still pass.
- Follow good frontend practice: minimal, scannable, status colors, no dead weight.
  Keep all state in memory (no localStorage).

## Done looks like
- `GET /ui` serves the page; identity bar shows Jordan / Onit.
- "Run market intelligence" enqueues a run; the list shows it go queued → succeeded within
  a few seconds (live worker), no refresh needed.
- Opening the succeeded run renders the full brief — targets table, keyword clusters,
  recommendations, citations.
- Approval queue renders its empty state cleanly.
- `smoke` green; committed and pushed.
