"""
Data model for the Agent HQ chassis.

Tenancy principle: every tenant-scoped table carries `org_id`. Onit is simply
the first row in `orgs`. App-layer scoping (see app/tenancy.py) is the primary
isolation guard and works on any database; Postgres RLS (see sql/schema.sql) is
defense-in-depth you switch on in production.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# Tenant root
# ---------------------------------------------------------------------------
class Org(Base, TimestampMixin):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200), unique=True, index=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin | member


# ---------------------------------------------------------------------------
# Connections to external platforms (ad networks, CRM, data providers)
# ---------------------------------------------------------------------------
class Connection(Base, TimestampMixin):
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    encrypted_token: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Agent registry. A row enables/configures an agent for one org. This is the
# "bring your own agent" seam: today kind="builtin" (maps to a code class);
# later kind="http" or "mcp" points at a teammate's endpoint with no rewrite.
# ---------------------------------------------------------------------------
class AgentRegistration(Base, TimestampMixin):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin | http | mcp
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(80), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Execution records
# ---------------------------------------------------------------------------
class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    agent_registration_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    agent_key: Mapped[str] = mapped_column(String(80))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # null => stub source (preserves prior behavior). Set when the trigger binds
    # this run to a user-uploaded account list (the CSV data-source seam).
    upload_id: Mapped[str | None] = mapped_column(ForeignKey("uploads.id"), nullable=True)
    # Per-run input the API caller supplied via TriggerRunIn.task. Read by the
    # agent through ctx.task (the content_engine uses it for action +
    # content_type + topic; market_intel ignores it). Was previously dropped.
    task: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[list] = mapped_column(JSON, default=list)

    artifacts: Mapped[list["Artifact"]] = relationship(backref="run")
    proposals: Mapped[list["Proposal"]] = relationship(backref="run")


class Upload(Base, TimestampMixin):
    """A user-uploaded account list (CSV). Tenant-scoped. Rows are stored
    parsed-and-normalized as JSON — the raw text is not retained (the mapped
    fields are what every consumer needs; storing both is duplicative)."""
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    uploaded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    # list of {name, employees, revenue_usd, industry} dicts after header mapping
    rows: Mapped[list] = mapped_column(JSON, default=list)


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    # Review status. "ready" is the default (preserves prior artifact behavior).
    # The content_engine sets "pending_review" when a draft is routed to the
    # approval queue; the queue approve/reject flips it to "ready" or "rejected".
    # NOTE: "ready" never means "published" — publishing is a separately-gated,
    # future action. See content_engine for the precedence rules.
    status: Mapped[str] = mapped_column(String(20), default="ready")
    # Version chain for content drafts: each "Give me something better" run
    # creates a NEW artifact whose parent_id points to the prior version. The
    # original is preserved (never destroyed) so the user can compare. Nullable
    # because the first draft (and all non-content artifacts) have no parent.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id"), nullable=True, index=True)
    # Advisory rubric grade (overall 0–100 + per-criterion + suggestions).
    # NULL when the agent didn't grade (e.g. non-content artifacts, market
    # briefs, etc.). Grades NEVER gate approval — see content_engine.
    grade: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # UTM tags — the join key a future analytics dashboard will use to
    # attribute performance back to the content that drove it. v1 generates
    # and surfaces the tagged link; publishing under it is a manual step the
    # user takes. Storing as first-class columns (not inside body) so the
    # join key is queryable + indexable. NULL for non-content artifacts.
    utm_campaign: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(60), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(160), nullable=True)
    destination_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class Proposal(Base, TimestampMixin):
    """A side-effect an agent wants to take (spend, publish). Always gated."""
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    guardrail_scope: Mapped[str | None] = mapped_column(String(20), nullable=True)
    guardrail_status: Mapped[str] = mapped_column(String(20), default="pending")
    guardrail_detail: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    approver_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Setup stage — durable, org-level profile (ICP, product, brand voice, etc).
# Filled three ways: manual form, derived from a website crawl, or sampled
# from a customer CSV. `confirmed` distinguishes a draft (returned by crawl /
# from-csv) from the user's saved truth (PUT /api/profile). Every downstream
# stage reasons from THIS object — keep it stable.
#
# CampaignBrief (future, separate, per-campaign object) will reference
# org_profiles.id. Do NOT build it here, but don't block it either —
# OrgProfile stays org-level config; per-campaign payloads belong elsewhere.
# ---------------------------------------------------------------------------
class OrgProfile(Base, TimestampMixin):
    __tablename__ = "org_profiles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True, unique=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    product_summary: Mapped[str] = mapped_column(Text, default="")
    value_prop: Mapped[str] = mapped_column(Text, default="")
    # icp shape: {industries:[], min_employees:int|None, min_revenue_usd:float|None,
    #             regions:[], titles:[], notes:str}
    icp: Mapped[dict] = mapped_column(JSON, default=dict)
    # list of {name, url?}
    competitors: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    brand_voice: Mapped[str] = mapped_column(Text, default="")
    banned_claims: Mapped[list] = mapped_column(JSON, default=list)
    conversion_goal: Mapped[str] = mapped_column(Text, default="")
    conversion_event: Mapped[str] = mapped_column(Text, default="")
    website_url: Mapped[str] = mapped_column(Text, default="")
    crawl_summary: Mapped[str] = mapped_column(Text, default="")
    # {field_name: "form"|"crawl"|"llm"|"csv"} — transparency over which fields
    # the user typed vs. which the system proposed. The UI flags non-"form"
    # fields as "suggested — confirm" until the user re-saves.
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    # False on every draft; only PUT /api/profile flips it true.
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Org-level routing for the DRAFT-REVIEW step of content generation.
    #   "all_through" — clean drafts auto-pass review (no queue entry).
    #   "guardrail"   — clean drafts pass; any draft that trips a guardrail
    #                   goes to the queue. (default)
    #   "gate_all"    — every draft goes to the queue for human approve/reject.
    # IMPORTANT: this governs DRAFT REVIEW only. It does NOT govern publishing
    # to any live channel — publishing is a separate, always-gated action and
    # is out of scope for v1. "all_through" means "draft marked ready", never
    # "auto-published".
    content_review_mode: Mapped[str] = mapped_column(String(20), default="guardrail")
    # Per-org content rubric: list of {name, description, weight?} criteria.
    # The grader scores each draft against these. Empty list means "use the
    # built-in default rubric" (see app/agents/content_grader.py:DEFAULT_RUBRIC)
    # — we don't persist defaults so that updating the constant flows through
    # to every org. Grades are ADVISORY: a low score is surfaced, never gates
    # approval (gating on a self-grade is a footgun — humans decide).
    content_rubric: Mapped[list] = mapped_column(JSON, default=list)


class Guardrail(Base, TimestampMixin):
    __tablename__ = "guardrails"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    rules: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base, TimestampMixin):
    """Append-only. Never update or delete rows here."""
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    target_type: Mapped[str] = mapped_column(String(80), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Durable job queue (Postgres/SQLite table the worker polls).
# ---------------------------------------------------------------------------
class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50), default="run_agent")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
