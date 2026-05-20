# Agent HQ (in this Repl) + MarketingHandoffAI (deployed)

This repo now holds **two** apps side by side:

1. **Agent HQ** — a Python/FastAPI multi-tenant chassis for running marketing
   agents with a human-in-the-loop approval queue. This is what the dev
   workflow runs. Onit is org #1 (dogfood).
2. **MarketingHandoffAI** — the original TypeScript/React/Express client portal.
   This is what the `.replit.app` production deploy serves. The code stays in
   `client/`, `server/`, `shared/`, `public/` and is built/run by the explicit
   `[deployment]` block in `.replit` — switching the dev workflow to Python
   does NOT affect the published site.

## User Preferences

Preferred communication style: Simple, everyday language.

---

## Agent HQ (dev — Python/FastAPI)

### Run it

The `Start application` workflow runs the API:
`uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload`

The `Worker` workflow runs the queue worker: `python -m app.worker`.

Bootstrap and verify:
```bash
python -m scripts.seed     # creates Onit (org #1), admin user, agent, guardrails
python -m scripts.smoke    # proves the whole spine + tenant isolation
```

Trigger a run:
```bash
curl -s -XPOST localhost:5000/api/runs \
  -H 'X-Dev-User-Email: jordan@onit.com' \
  -H 'content-type: application/json' \
  -d '{"agent_key":"market_intel"}'
curl -s localhost:5000/api/runs/<RUN_ID> -H 'X-Dev-User-Email: jordan@onit.com'
```

### Architecture
- **Contract** (`app/schemas.py`): every agent takes an `AgentContext` and
  returns an `AgentResult`. This is the BYO-agent seam — `kind="http"`/`"mcp"`
  is a v3 config change, not a rewrite.
- **Tenancy** (`app/tenancy.py`): `scoped(Model, org_id)` is the only way to
  read tenant tables — it raises if you forget `org_id`. The two legitimate
  exceptions (bootstrap lookup in `auth.py`, system-level setup in
  `scripts/seed.py` + `scripts/smoke.py`) are documented in those files.
- **Queue + worker**: API enqueues a `Job`, worker leases and runs the agent
  in a separate process. Graceful failure: a bad run is marked failed; the
  loop never crashes.
- **Guardrails** (`app/guardrails.py`): every `ProposedAction` is evaluated
  before it could execute. `market_intel` is read-only (zero proposals) — the
  framework is wired but not exercised hard until P2 brings action-takers.
- **Data sources** (`app/data_sources/`): `MarketDataSource` interface;
  `StubMarketDataSource` today, ZoomInfo/Apollo behind the same seam later.
- **Audit log**: append-only; the worker writes a `run.succeeded` or
  `run.failed` row for every run.

### Database
- **Dev**: SQLite at `agenthq.db` (WAL + busy_timeout for API + worker
  concurrency, configured in `app/db.py`). Created automatically by the API on
  startup.
- **Prod**: set `AGENT_HQ_DATABASE_URL` (NOT `DATABASE_URL` — that one belongs
  to the legacy TS app) to a Postgres URL and apply `sql/schema.sql`, which
  also installs RLS policies as defense-in-depth.

### Auth
Dev trusts the `X-Dev-User-Email` header (or `DEV_AUTH_EMAIL`). Prod swap:
replace `app/auth.py::current_user` with Google Workspace SSO that verifies
the `hd` domain claim against `ALLOWED_EMAIL_DOMAINS`.

### Project layout (Python side)
```
app/
  main.py               FastAPI app + /healthz
  config.py             pydantic-settings (reads AGENT_HQ_DATABASE_URL)
  db.py                 SQLAlchemy engine (SQLite WAL or psycopg v3 for PG)
  models.py             SQLAlchemy models for all tenant tables
  tenancy.py            scoped() helper — the load-bearing isolation guard
  auth.py               dev header auth; swap to SSO in prod
  queue.py              enqueue / lease_next / complete (Job table)
  guardrails.py         policy evaluation for ProposedAction
  schemas.py            AgentContext / AgentResult contract + API DTOs
  worker.py             standalone process, polls jobs, runs agents
  agents/
    base.py registry.py synthesis.py market_intel.py
  data_sources/
    base.py stub.py
  api/
    runs.py review.py admin.py
scripts/seed.py scripts/smoke.py
sql/schema.sql          Postgres DDL + RLS (reference for prod)
```

### Known P0 gaps (documented, not blocking)
- `TriggerRunIn.task` is accepted but not persisted on the `Run` — fine for
  `market_intel` which ignores `task`; persist before adding an agent that
  needs per-run input.
- SQLite is solo-dev only — flip to Postgres before the team logs in.

---

## MarketingHandoffAI (production deploy — TypeScript)

The deployed `.replit.app` site is unchanged. It still runs the React/Express
client portal with Stripe and Replit Auth.

- Frontend: React 18, Vite, Wouter, TanStack Query, Tailwind, shadcn/ui.
- Backend: Express + TypeScript; Replit Auth via OpenID Connect; PostgreSQL
  via Drizzle ORM (`shared/schema.ts`, `shared/models/auth.ts`).
- Stripe: `stripe-replit-sync` for managed sync; pricing tiers $5k / $10k / $15k.
- Build/run: `npm run build` → `node ./dist/index.cjs`, declared explicitly in
  `.replit`'s `[deployment]` block so it doesn't inherit the dev workflow.
- Source: `client/`, `server/`, `shared/`, `public/`, `package.json` — all
  untouched by the Agent HQ work.
