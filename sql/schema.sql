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
    -- Per-org content rubric: list of {name, description, weight?} criteria.
    -- Empty list means "use the built-in DEFAULT_RUBRIC from code". Grades
    -- are ADVISORY only — they never gate approval.
    content_rubric    JSONB NOT NULL DEFAULT '[]',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tenant identity — strictly PRESENTATION. Brand tokens feed the
-- chrome (logo, color roles, fonts); they NEVER enter the
-- intelligence object, the evidence ledger, or any input a §6/§7
-- validator reads. One row per org via UNIQUE org_id.
CREATE TABLE org_brands (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL UNIQUE REFERENCES orgs(id) ON DELETE CASCADE,
    color_primary     TEXT NOT NULL DEFAULT '#1f3b6b',
    color_secondary   TEXT NOT NULL DEFAULT '#2d8c5a',
    color_accent      TEXT NOT NULL DEFAULT '#c89a3a',
    color_background  TEXT NOT NULL DEFAULT '#0e1218',
    color_text        TEXT NOT NULL DEFAULT '#e6e9f0',
    font_heading      TEXT NOT NULL DEFAULT 'Inter',
    font_body         TEXT NOT NULL DEFAULT 'Inter',
    -- Logo file ref is RELATIVE to settings.storage_root; the layout
    -- is {storage_root}/{org_id}/_brand/logo.<ext> so a scoped()
    -- lookup error can't lead to serving the wrong tenant's logo.
    logo_path         TEXT,
    logo_mime         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fan-out orchestration record. One row per "spawn N derivatives from
-- one anchor" request. Carries the SELECTED LEAD (an ev:id pointer
-- into the anchor's ledger — NEVER newly-authored prose) and the
-- list of spawned child runs. Trust lives on each child artifact
-- (independent §7 gate); the set has no trust_checks of its own.
-- See app/reports/derivatives/leads.py + the fan-out brief.
CREATE TABLE fanout_sets (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    source_anchor_id    TEXT NOT NULL,
    source_anchor_type  TEXT,
    source_anchor_title TEXT,
    lead_ev_id          TEXT NOT NULL,
    lead_payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    children            JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fanout_sets_org ON fanout_sets(org_id);
CREATE INDEX idx_fanout_sets_source ON fanout_sets(source_anchor_id);

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
    -- OPTIONAL product scope. NULL = org-level run. ON DELETE SET NULL
    -- means deleting a product reverts existing rows to org-level instead
    -- of erasing the run.
    product_id            TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
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
    -- OPTIONAL product scope (mirrors runs.product_id).
    product_id  TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        JSONB NOT NULL DEFAULT '{}',
    citations   JSONB NOT NULL DEFAULT '[]',
    -- Review status: 'ready' | 'pending_review' | 'rejected'. Default 'ready'
    -- preserves the existing market_intel path. content_engine sets
    -- 'pending_review' when routing a draft to the approval queue; the queue
    -- decision flips it to 'ready' or 'rejected'. "ready" != "published".
    status      TEXT NOT NULL DEFAULT 'ready',
    -- Version chain for content drafts. Each "Give me something better" run
    -- creates a NEW artifact whose parent_id points to the prior version.
    -- The original is preserved so the user can compare versions.
    parent_id   TEXT REFERENCES artifacts(id),
    -- Advisory rubric grade (overall + per-criterion + suggestions). Grades
    -- never gate approval — they surface signal for the human reviewer.
    grade       JSONB,
    -- UTM tags as first-class columns so a future analytics dashboard can
    -- JOIN performance rows back to the artifact that produced them.
    -- v1 only generates the tagged link; the human publishes under it.
    utm_campaign     TEXT,
    utm_source       TEXT,
    utm_medium       TEXT,
    utm_content      TEXT,
    destination_url  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE proposals (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES orgs(id),
    run_id           TEXT NOT NULL REFERENCES runs(id),
    -- OPTIONAL product scope (mirrors runs.product_id).
    product_id       TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
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

-- ---- Product layer ------------------------------------------------------
-- A product is a child of an org with its own profile. Every downstream
-- object carries an OPTIONAL product_id so it can be scoped to a product OR
-- remain org-level (product_id IS NULL). app/products.resolve_product_profile
-- is the single source of truth for the inheritance merge.
CREATE TABLE product_profiles (
    id                       TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL REFERENCES orgs(id),
    name                     TEXT NOT NULL,
    slug                     TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'draft',
    website_url              TEXT NOT NULL DEFAULT '',
    -- product-only fields (no inheritance)
    positioning              TEXT NOT NULL DEFAULT '',
    target_persona           JSONB NOT NULL DEFAULT '{}',
    value_props              JSONB NOT NULL DEFAULT '[]',
    proof_points             JSONB NOT NULL DEFAULT '[]',
    differentiators          JSONB NOT NULL DEFAULT '[]',
    key_features             JSONB NOT NULL DEFAULT '[]',
    use_cases                JSONB NOT NULL DEFAULT '[]',
    product_competitors      JSONB NOT NULL DEFAULT '[]',
    -- inheritable overrides: NULL = inherit from org, non-null = product wins
    brand_voice_override     TEXT,
    banned_claims_override   JSONB,
    conversion_goal_override TEXT,
    rubric_override          JSONB,
    utm_source_default       TEXT,
    utm_medium_default       TEXT,
    -- Lightweight, schemaless sections fed by document-extraction's
    -- "messaging notes" path (objection_handling, launch_messaging). The
    -- content engine reads these at generation time. See models.py.
    messaging_notes          JSONB NOT NULL DEFAULT '{}',
    -- Append-only log of accepted product-knowledge values per field.
    -- Each entry: {field, value, accepted_from_insight_id, accepted_at,
    -- status: active|superseded|historical}. Supports rollback + audit.
    field_history            JSONB NOT NULL DEFAULT '[]',
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, slug)
);
CREATE INDEX idx_product_profiles_org ON product_profiles(org_id);

-- ---- Document ingestion -------------------------------------------------
-- product_documents stores the raw uploaded docs (raw file on disk under
-- a tenant-scoped path; this row carries the metadata). Each upload
-- enqueues an extraction job on the worker, which normalizes text and
-- calls the LLM to produce extracted_insights candidates.
CREATE TABLE product_documents (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES orgs(id),
    product_id        TEXT NOT NULL REFERENCES product_profiles(id) ON DELETE CASCADE,
    filename          TEXT NOT NULL,
    mime_type         TEXT NOT NULL DEFAULT '',
    size_bytes        INTEGER NOT NULL DEFAULT 0,
    sha256            TEXT NOT NULL DEFAULT '',
    -- Tenant-scoped path under settings.storage_root.
    storage_path      TEXT NOT NULL DEFAULT '',
    -- 'messaging_framework' | 'one_pager' | 'launch_doc' |
    -- 'sales_enablement' | 'other' (user-tagged at upload).
    kind              TEXT NOT NULL DEFAULT 'messaging_framework',
    version_label     TEXT NOT NULL DEFAULT '',
    -- 'ingesting' | 'extracted' | 'failed' | 'superseded'.
    status            TEXT NOT NULL DEFAULT 'ingesting',
    extracted_text    TEXT,
    extraction_error  TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_product_documents_org_product
    ON product_documents(org_id, product_id);
CREATE INDEX idx_product_documents_sha256 ON product_documents(org_id, sha256);

-- extracted_insights is the candidate review queue. Each row is one
-- (field × document × extraction-run) pending human review. Accepted/
-- edited rows promote into ProductProfile via the additive merge.
-- Extraction MUST NOT produce candidates for any *_override field — the
-- application layer enforces this in app/documents/extract.py.
CREATE TABLE extracted_insights (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES orgs(id),
    product_id          TEXT NOT NULL REFERENCES product_profiles(id) ON DELETE CASCADE,
    product_document_id TEXT NOT NULL REFERENCES product_documents(id) ON DELETE CASCADE,
    field_name          TEXT NOT NULL,
    value               JSONB NOT NULL DEFAULT '{}',
    confidence          DOUBLE PRECISION NOT NULL DEFAULT 0,
    -- Verbatim quote from the doc that motivated the value.
    source_passage      TEXT NOT NULL DEFAULT '',
    -- {"page": N} | {"slide": N} | {"paragraph": N} | {"via": "ocr"}.
    source_location     JSONB,
    -- Reserved for future variant scoping (persona/industry/segment/...).
    -- v1: always null. The column exists; no logic branches on it.
    dimensions          JSONB,
    -- 'pending' | 'accepted' | 'edited' | 'rejected' | 'superseded'.
    status              TEXT NOT NULL DEFAULT 'pending',
    accepted_value      JSONB,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_extracted_insights_org_product
    ON extracted_insights(org_id, product_id);
CREATE INDEX idx_extracted_insights_status
    ON extracted_insights(org_id, product_id, status);
CREATE INDEX idx_extracted_insights_doc
    ON extracted_insights(org_id, product_document_id);

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

-- ---- Analytics dashboard ------------------------------------------------
-- Generic time-series receptacle: users upload reports their tools already
-- export, we normalize each row to a point. Curated funnel + production
-- lane views compose on top (app/dashboard/funnel.py). UTM fields are the
-- join key back to artifacts.utm_*.

CREATE TABLE report_uploads (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(id),
    filename        TEXT NOT NULL,
    -- "ga" | "semrush" | "linkedin_ads" | "manual" | ...
    source          TEXT NOT NULL DEFAULT 'manual',
    -- 'baseline' = backdrop (never attributed); 'ongoing' = attributed where
    -- the row carries a UTM matching produced content.
    mode            TEXT NOT NULL DEFAULT 'ongoing',
    column_mapping  JSONB NOT NULL DEFAULT '{}',
    point_count     INTEGER NOT NULL DEFAULT 0,
    uploaded_by     TEXT,
    -- OPTIONAL product scope: an upload can be flagged as belonging to a
    -- specific product so its rows flow into that product's funnel view.
    product_id      TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE metric_points (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES orgs(id),
    report_upload_id  TEXT REFERENCES report_uploads(id),
    source            TEXT NOT NULL DEFAULT 'manual',
    metric_name       TEXT NOT NULL,
    value             DOUBLE PRECISION NOT NULL DEFAULT 0,
    date              DATE NOT NULL,
    segment           TEXT NOT NULL DEFAULT '',
    -- The join key to artifacts.utm_*. Present only when the report carried
    -- it (typical for 'ongoing' uploads; baseline rows are usually untagged).
    utm_campaign      TEXT,
    utm_source        TEXT,
    utm_medium        TEXT,
    utm_content       TEXT,
    raw_ref           JSONB NOT NULL DEFAULT '{}',
    -- True for points from a 'baseline' upload — backdrop only, never
    -- attributed to produced content.
    is_baseline       BOOLEAN NOT NULL DEFAULT false,
    -- OPTIONAL product scope (inherited from the parent ReportUpload).
    product_id        TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE suggestions (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES orgs(id),
    -- 'trend' (grounded in this org's data) | 'industry' (LLM perspective,
    -- explicitly labeled non-data).
    kind                TEXT NOT NULL,
    recommendation      TEXT NOT NULL,
    -- Evidence is required: a suggestion without its "because" is not
    -- allowed by the brief. Carries the specific data motivating it.
    evidence            JSONB NOT NULL DEFAULT '{}',
    confidence          TEXT NOT NULL DEFAULT 'medium',
    source_label        TEXT NOT NULL DEFAULT '',
    idea_content_type   TEXT,
    idea_topic          TEXT,
    idea_target         TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    -- OPTIONAL product scope: suggestions from a product-filtered funnel.
    product_id          TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_metric_points_org_date    ON metric_points(org_id, date);
CREATE INDEX idx_metric_points_campaign    ON metric_points(org_id, utm_campaign);
CREATE INDEX idx_metric_points_metric_name ON metric_points(org_id, metric_name);
CREATE INDEX idx_metric_points_product     ON metric_points(org_id, product_id);
CREATE INDEX idx_report_uploads_org        ON report_uploads(org_id, created_at);
CREATE INDEX idx_suggestions_org_status    ON suggestions(org_id, status);
CREATE INDEX idx_suggestions_product       ON suggestions(org_id, product_id);
CREATE INDEX idx_runs_product              ON runs(org_id, product_id);
CREATE INDEX idx_artifacts_product         ON artifacts(org_id, product_id);
CREATE INDEX idx_proposals_product         ON proposals(org_id, product_id);

-- ---- Campaigns (Phase 3 / step 4) ----------------------------------------
-- Orchestration layer. Campaigns REFERENCE artifacts; they do not own
-- generation logic — the content engine is still the only generator.
-- See app/models.py for the workflow.
CREATE TABLE campaigns (
    id                       TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL REFERENCES orgs(id),
    product_id               TEXT REFERENCES product_profiles(id) ON DELETE SET NULL,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    -- awareness | demand_gen | launch | nurture | competitive | other
    campaign_type            TEXT NOT NULL DEFAULT 'other',
    objective                TEXT NOT NULL DEFAULT '',
    -- draft | planned | generating | active | complete | archived
    status                   TEXT NOT NULL DEFAULT 'draft',
    owner                    TEXT NOT NULL DEFAULT '',
    start_date               DATE,
    end_date                 DATE,
    parent_asset_id          TEXT REFERENCES artifacts(id) ON DELETE SET NULL,
    primary_cta              TEXT NOT NULL DEFAULT '',
    target_personas          JSONB NOT NULL DEFAULT '[]',
    target_segments          JSONB NOT NULL DEFAULT '[]',
    target_industries        JSONB NOT NULL DEFAULT '[]',
    target_account_ref       JSONB,
    selected_channels        JSONB NOT NULL DEFAULT '[]',
    channel_recommendations  JSONB NOT NULL DEFAULT '{}',
    channel_notes            JSONB,
    plan                     JSONB NOT NULL DEFAULT '{}',
    generated_asset_ids      JSONB NOT NULL DEFAULT '[]',
    utm_campaign             TEXT NOT NULL DEFAULT '',
    -- Performance hooks RESERVED — present, unused, no branching in v1.
    -- Same discipline as the dimensions column from Build B.
    kpis                     JSONB,
    linked_metric_point_query JSONB,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_campaigns_org_status      ON campaigns(org_id, status);
CREATE INDEX idx_campaigns_product         ON campaigns(org_id, product_id);

-- Back-references on the content tables — nullable so artifacts /
-- proposals remain independent of any campaign (archiving a campaign
-- SET NULLs the back-ref; the asset stays in the library).
ALTER TABLE artifacts ADD COLUMN campaign_id TEXT
    REFERENCES campaigns(id) ON DELETE SET NULL;
ALTER TABLE proposals ADD COLUMN campaign_id TEXT
    REFERENCES campaigns(id) ON DELETE SET NULL;
CREATE INDEX idx_artifacts_campaign        ON artifacts(org_id, campaign_id);
CREATE INDEX idx_proposals_campaign        ON proposals(org_id, campaign_id);

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
                           'org_profiles','org_brands','fanout_sets',
                           'report_uploads','metric_points',
                           'suggestions','product_profiles','product_documents',
                           'extracted_insights','campaigns']
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
