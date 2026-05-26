-- Agent HQ — Postgres schema with Row-Level Security (defense-in-depth).
--
-- App-layer scoping (app/tenancy.py) is the PRIMARY tenant guard and runs on
-- any database. This file adds RLS so that even a buggy or forgotten WHERE
-- clause cannot leak across orgs at the database level.
--
-- Usage in production (Replit Postgres):
--   1. Run this file once.
--   2. Per request/worker job, set the tenant before querying:
--        SET app.current_org = '<org_id>';
--      (Wrap this in your DB session setup so it's automatic.)
--
-- This is reference DDL mirroring app/models.py. SQLAlchemy create_all() is for
-- dev/SQLite; in prod, run this instead.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE orgs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    email       TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'member',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE connections (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(id),
    provider        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'disconnected',
    encrypted_token TEXT NOT NULL DEFAULT '',
    config          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES orgs(id),
    key           TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'builtin',
    endpoint      TEXT NOT NULL DEFAULT '',
    enabled       BOOLEAN NOT NULL DEFAULT true,
    schedule_cron TEXT,
    config        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE uploads (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES orgs(id),
    filename     TEXT NOT NULL,
    uploaded_by  TEXT,
    row_count    INTEGER NOT NULL DEFAULT 0,
    rows         JSONB NOT NULL DEFAULT '[]',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Setup stage: durable org-level profile. CampaignBrief (per-campaign, future)
-- will reference org_profiles.id — kept separate on purpose.
CREATE TABLE org_profiles (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL UNIQUE REFERENCES orgs(id),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    product_summary   TEXT NOT NULL DEFAULT '',
    value_prop        TEXT NOT NULL DEFAULT '',
    icp               JSONB NOT NULL DEFAULT '{}',
    competitors       JSONB NOT NULL DEFAULT '[]',
    keywords          JSONB NOT NULL DEFAULT '[]',
    brand_voice       TEXT NOT NULL DEFAULT '',
    banned_claims     JSONB NOT NULL DEFAULT '[]',
    conversion_goal   TEXT NOT NULL DEFAULT '',
    conversion_event  TEXT NOT NULL DEFAULT '',
    website_url       TEXT NOT NULL DEFAULT '',
    crawl_summary     TEXT NOT NULL DEFAULT '',
    source            JSONB NOT NULL DEFAULT '{}',
    confirmed         BOOLEAN NOT NULL DEFAULT false,
    -- Routing for the DRAFT-REVIEW step of content generation. See models.py.
    -- Governs DRAFT review only — publishing to a live channel is always a
    -- separate, always-gated action and is out of scope for v1.
    content_review_mode TEXT NOT NULL DEFAULT 'guardrail',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    id                    TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL REFERENCES orgs(id),
    agent_registration_id TEXT NOT NULL REFERENCES agents(id),
    agent_key             TEXT NOT NULL,
    trigger               TEXT NOT NULL DEFAULT 'manual',
    status                TEXT NOT NULL DEFAULT 'queued',
    cost_usd              DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_by            TEXT,
    upload_id             TEXT REFERENCES uploads(id),
    -- Per-run input passed in by the API caller via TriggerRunIn.task.
    -- Read by the agent through ctx.task.
    task                  JSONB NOT NULL DEFAULT '{}',
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    error                 TEXT,
    logs                  JSONB NOT NULL DEFAULT '[]',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE artifacts (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    run_id      TEXT NOT NULL REFERENCES runs(id),
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        JSONB NOT NULL DEFAULT '{}',
    citations   JSONB NOT NULL DEFAULT '[]',
    -- Review status: 'ready' | 'pending_review' | 'rejected'. Default 'ready'
    -- preserves the existing market_intel path. content_engine sets
    -- 'pending_review' when routing a draft to the approval queue; the queue
    -- decision flips it to 'ready' or 'rejected'. "ready" != "published".
    status      TEXT NOT NULL DEFAULT 'ready',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE proposals (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES orgs(id),
    run_id           TEXT NOT NULL REFERENCES runs(id),
    action_type      TEXT NOT NULL,
    payload          JSONB NOT NULL DEFAULT '{}',
    guardrail_scope  TEXT,
    guardrail_status TEXT NOT NULL DEFAULT 'pending',
    guardrail_detail TEXT NOT NULL DEFAULT '',
    reasoning        TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',
    approver_id      TEXT,
    decided_at       TIMESTAMPTZ,
    executed_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE guardrails (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    scope       TEXT NOT NULL,
    rules       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id   TEXT NOT NULL DEFAULT '',
    meta        JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    kind        TEXT NOT NULL DEFAULT 'run_agent',
    payload     JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'queued',
    attempts    INTEGER NOT NULL DEFAULT 0,
    lease_until TIMESTAMPTZ,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_org      ON users(org_id);
CREATE INDEX idx_runs_org       ON runs(org_id);
CREATE INDEX idx_artifacts_run  ON artifacts(run_id);
CREATE INDEX idx_proposals_org  ON proposals(org_id, status);
CREATE INDEX idx_jobs_status    ON jobs(status, created_at);
CREATE INDEX idx_uploads_org    ON uploads(org_id);
CREATE INDEX idx_org_profiles_org ON org_profiles(org_id);

-- ---- Row-Level Security -------------------------------------------------
-- Enable on every tenant table and bind reads/writes to the current org GUC.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','connections','agents','runs','artifacts',
                           'proposals','guardrails','audit_log','jobs','uploads',
                           'org_profiles']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
    EXECUTE format($p$
      CREATE POLICY tenant_isolation ON %I
      USING (org_id = current_setting('app.current_org', true))
      WITH CHECK (org_id = current_setting('app.current_org', true));
    $p$, t);
  END LOOP;
END $$;
