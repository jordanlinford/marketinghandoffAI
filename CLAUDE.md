# CLAUDE.md — Agent HQ

Read this first, every session. These are project rules, not suggestions.

## What this is
Agent HQ: a multi-tenant Python/FastAPI chassis that runs marketing agents with a
human approval queue. Onit is org #1 (dogfood). It lives ALONGSIDE an existing
deployed TypeScript app in this same repo. P0 ships one read-only agent
(`market_intel`) end to end. Action-taking agents (spend/publish) are P2.

## Stack & commands
- Python 3.11, FastAPI, SQLAlchemy 2.0, Pydantic 2. Dev DB = SQLite; prod = Postgres.
- Seed:   `python -m scripts.seed`
- Test:   `python -m scripts.smoke`   ← must pass before every commit
- API:    `uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload`
- Worker: `python -m app.worker`  (separate process; both run via the "Project" workflow)

## The test gate (non-negotiable)
`python -m scripts.smoke` must stay green. It proves the full spine (trigger → queue →
worker → cited artifact) AND tenant isolation (a second org sees zero of Onit's runs).
Never commit with smoke red. As features grow, grow smoke into a pytest suite — tests
are the accuracy guarantee, not eyeballing diffs.

## The four constraints (do not drift)
1. **`scoped()` is the only way to read tenant tables.** Use
   `scoped(Model, org_id)` from `app/tenancy.py` — never a bare `select(Model)` against
   users/runs/artifacts/proposals/etc. `scoped()` raises on a missing `org_id`, so a
   forgotten filter fails loudly instead of leaking another org's data.
2. **Agents take `AgentContext`, return `AgentResult` — nothing else.** Agents read data
   ONLY through `ctx.get_market_data()` (the `MarketDataSource` interface). Never import a
   vendor SDK (ZoomInfo/Apollo) inside an agent. This is the BYO-agent seam.
3. **Guardrails gate every proposed action before execution.** The worker evaluates each
   `ProposedAction` against the org's guardrails before persisting it. `market_intel`
   returns zero proposals, but the framework stays in place from day one.
4. **The worker is a separate process.** The API enqueues; the worker leases and runs.
   Never run an agent inline in an API handler.

## Legitimate `scoped()` exceptions — DO NOT "fix" these
- `app/auth.py` does a raw `select(User).where(User.email == ...)` because it must resolve
  who the user is BEFORE it knows their org (the pre-tenant bootstrap). Correct as-is.
- `scripts/seed.py` and `scripts/smoke.py` use raw selects — they run as system-level
  setup, not as a tenant request. Correct as-is.
Routing either through `scoped()` creates a chicken-and-egg break.

## Tenancy
`org_id` is on every tenant table; this is multi-tenant from line one (Onit is just the
first `orgs` row). Never key tenancy off client input — derive the org from the
SSO-verified email domain. In dev, auth trusts the `X-Dev-User-Email` header; prod swaps in
Google Workspace SSO (verify the `hd` domain claim, derive org from domain).

## Database
Dev = SQLite with WAL + `busy_timeout` (required because the API and Worker are two
processes hitting one file — without it you get "database is locked"). Prod = Postgres via
`sql/schema.sql`, which also installs row-level security. RLS is NOT live until
`SET app.current_org = '<org_id>'` is wired into `get_db` and the worker — until then,
app-layer `scoped()` is the only guard.

## Don't touch the TS app
Leave `client/`, `server/`, `shared/`, `public/`, `package.json`, and the `[deployment]`
block in `.replit` untouched. The published site builds and runs the TS app
(`build: npm run build`, `run: node ./dist/index.cjs`). The Python chassis is dev-only.

## Data-source seam
`market_intel` uses `StubMarketDataSource` today. To go live, write
`ZoomInfoDataSource`/`ApolloDataSource` implementing `MarketDataSource` and return them from
`app/worker.py::_resolve_market_data`. The agent never changes.

## Known gaps to respect (don't be surprised, don't silently "fix")
- `TriggerRunIn.task` is currently dropped — not persisted on the `Run`, not passed to
  `ctx.task`. Harmless for `market_intel` (ignores it). Wire it through when an agent needs
  per-run input.
- RLS not live until the `SET app.current_org` wiring above exists.

## Roadmap
- P0 (done): chassis + `market_intel`, end to end.
- P1 (next): review UI for the approval queue + harden the five fundamentals + onboard team.
- P2: action-taking agents (content/publish gate, spend guardrails) + real ZoomInfo/Apollo.
- P3: open the registry to `http`/`mcp` BYO agents (untrusted-code sandboxing deferred to here).

## The rule that keeps this file useful
Every time you do something wrong and I correct it, ADD A RULE here so it never repeats.
Keep this file ~100 lines — it loads every session.
