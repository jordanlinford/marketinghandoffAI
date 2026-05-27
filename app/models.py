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

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint)
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
    # OPTIONAL product scope. NULL = org-level run (current default for
    # everything that existed before the product layer landed). When set,
    # the worker hands the agent a resolved profile that inherits from the
    # org and applies any product overrides. ondelete=SET NULL: deleting a
    # product is a soft event for already-emitted artifacts — they revert
    # to org-level rather than disappearing.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True)
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
    # OPTIONAL product scope (mirrors Run.product_id). NULL = org-level.
    # Set by the worker from run.product_id so attribution + filtering work
    # consistently across the dashboard.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True)
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
    # OPTIONAL product scope (mirrors Run.product_id). NULL = org-level.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True)
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


# ---------------------------------------------------------------------------
# Product layer — a child of the org with its own profile. Every downstream
# object (runs, artifacts, proposals, metric_points, suggestions,
# report_uploads) carries an OPTIONAL product_id so it can be scoped to a
# product OR remain org-level (product_id IS NULL) — the historical default.
#
# Inheritance:
#   - Product-only fields live ONLY here (positioning, persona, value_props,
#     etc.) — there is no org-level equivalent.
#   - "Inheritable overrides" are NULL by default, which means "inherit from
#     OrgProfile". When set, the product overrides the org. The single
#     source of truth that does the merge is app/products.resolve_product_profile().
#
# Cross-tenant safety: scoped() on org_id. The (org_id, slug) unique
# constraint keeps URLs / UTM prefixes unambiguous per org.
# ---------------------------------------------------------------------------
class ProductProfile(Base, TimestampMixin):
    __tablename__ = "product_profiles"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_product_slug_per_org"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    # draft → confirmed mirrors the OrgProfile pattern: a draft isn't read
    # by agents until the user explicitly confirms it. Lets the UI surface
    # "this product is still a draft" without leaking unfinished positioning
    # into a run.
    status: Mapped[str] = mapped_column(String(20), default="draft")
    website_url: Mapped[str] = mapped_column(Text, default="")

    # ---- Product-only fields (no org equivalent; no inheritance) -------
    positioning: Mapped[str] = mapped_column(Text, default="")
    # Persona is small structured data — role / seniority / pains. JSON
    # rather than columns so PMM can iterate on shape without a migration.
    target_persona: Mapped[dict] = mapped_column(JSON, default=dict)
    value_props: Mapped[list] = mapped_column(JSON, default=list)
    proof_points: Mapped[list] = mapped_column(JSON, default=list)
    differentiators: Mapped[list] = mapped_column(JSON, default=list)
    key_features: Mapped[list] = mapped_column(JSON, default=list)
    use_cases: Mapped[list] = mapped_column(JSON, default=list)
    # Distinct from OrgProfile.competitors — these are the competitors for
    # THIS product specifically (the broader org may compete with others).
    product_competitors: Mapped[list] = mapped_column(JSON, default=list)

    # ---- Inheritable overrides: NULL = inherit; non-null = product wins -
    # Each of these has a counterpart on OrgProfile that the resolver falls
    # back to when the override is None. NEVER read these directly from an
    # agent — go through resolve_product_profile().
    brand_voice_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_claims_override: Mapped[list | None] = mapped_column(JSON, nullable=True)
    conversion_goal_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric_override: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # UTM defaults — the content engine prefers these over the per-content-
    # type channel defaults, so a product's pieces share a coherent channel
    # signature in reports.
    utm_source_default: Mapped[str | None] = mapped_column(String(60), nullable=True)
    utm_medium_default: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Lightweight, schemaless sections fed by extraction's "messaging notes"
    # path. Shape today:
    #   {"objection_handling": [{note, source_passage?, source_doc_id?}, ...],
    #    "launch_messaging":   [...]}
    # JSON because we want to learn the shape from real usage before
    # normalizing it. The content_engine consults objection_handling at
    # generation time; see app/agents/content_engine.py.
    messaging_notes: Mapped[dict] = mapped_column(JSON, default=dict)

    # Append-only history of accepted product-knowledge values. Each entry:
    #   {field, value, accepted_from_insight_id, accepted_at, status}
    # status ∈ {active | superseded | historical}. New accepted value flips
    # the prior active entry for that field to superseded; nothing is ever
    # deleted. Supports rollback ("re-activate prior positioning"), audit
    # ("what changed when"), and future correlation hooks — one column,
    # zero new tables.
    field_history: Mapped[list] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


# ---------------------------------------------------------------------------
# Document ingestion (Phase 1, Build B). The product layer absorbs PMM
# frameworks, one-pagers, launch docs, etc. as durable structured product
# intelligence — not a RAG corpus. The flow is:
#   upload (ProductDocument) → worker normalize text → LLM extract →
#   ExtractedInsight rows (status=pending) → human review (accept/edit/
#   reject) → promote into ProductProfile via the additive merge +
#   ProductProfile.field_history.
# Agents NEVER read ProductDocument / ExtractedInsight directly — those
# are review-layer concerns. Agents read the resolved profile, which is
# richer thanks to accepted promotions.
# ---------------------------------------------------------------------------
class ProductDocument(Base, TimestampMixin):
    __tablename__ = "product_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # SHA-256 of the raw bytes for dedupe + integrity. Stored even if a
    # later "re-upload same file" lands; the API can decide to short-circuit.
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Tenant-scoped path under settings.storage_root. The raw file is on disk
    # (not in the DB) so we keep the row light.
    storage_path: Mapped[str] = mapped_column(Text, default="")
    # User-tagged at upload; the extractor uses this to emphasize the right
    # canonical fields (a launch_doc weights launch_messaging higher, etc.).
    # 'messaging_framework' | 'one_pager' | 'launch_doc' |
    # 'sales_enablement' | 'other'.
    kind: Mapped[str] = mapped_column(String(40), default="messaging_framework")
    version_label: Mapped[str] = mapped_column(String(120), default="")
    # 'ingesting' (queued) | 'extracted' | 'failed' | 'superseded'.
    # 'superseded' is set when a newer doc of the same kind for the same
    # product is uploaded — the older doc + its insights remain for audit;
    # only "active extraction" moves on.
    status: Mapped[str] = mapped_column(String(20), default="ingesting", index=True)
    # The normalized text we pulled from the raw doc, so re-extraction can
    # run without re-uploading. Nullable while status=ingesting/failed.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated when ingestion or extraction fails — surfaces in the UI as
    # an actionable error (e.g. "Tesseract OCR not installed").
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


class ExtractedInsight(Base, TimestampMixin):
    """One candidate (field × document × extraction run). Pending until a
    human accepts / edits / rejects. Accepted/edited rows promote into the
    parent ProductProfile via the additive merge."""
    __tablename__ = "extracted_insights"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="CASCADE"), index=True)
    product_document_id: Mapped[str] = mapped_column(
        ForeignKey("product_documents.id", ondelete="CASCADE"), index=True)
    # Must be one of the canonical ProductProfile target fields OR a
    # messaging-notes target name. Extraction MUST NOT produce candidates
    # for *_override fields — overriding inheritance is a deliberate human
    # decision. Enforced in app/documents/extract.py + asserted in smoke.
    field_name: Mapped[str] = mapped_column(String(80), index=True)
    # JSON because the value shape varies by field (string for positioning,
    # list for value_props, dict for target_persona).
    value: Mapped[dict | list | str] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # Verbatim quote from the doc that motivated the value — capped to a
    # short passage. Persisted unparaphrased so the user can audit the
    # model's reasoning, the same way market_brief carries citations.
    source_passage: Mapped[str] = mapped_column(Text, default="")
    # Best-effort source-location anchor: {"page": N} | {"slide": N} |
    # {"paragraph": N} | {"via": "ocr"}. May be null for OCR.
    source_location: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Reserved for future variant scoping (persona/industry/segment/...).
    # v1: ALWAYS null. The column exists; no logic branches on it.
    # See docs/document-ingestion-brief.md for the deferred ontology.
    dimensions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 'pending' | 'accepted' | 'edited' | 'rejected' | 'superseded'.
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Populated when status=accepted/edited. If edited, this is the user's
    # value (the source_passage stays as the original verbatim quote for
    # audit). Promotion always uses accepted_value, not value.
    accepted_value: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


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
# Analytics dashboard — generic time-series receptacle.
#
# The dashboard is a RECEPTACLE, not a connector. Users upload the reports
# their tools already export (GA / SEMrush / LinkedIn Ads / ...) and the
# normalized rows land in `metric_points`. The shape is intentionally
# generic — different tools export wildly different metrics — and the
# curated funnel view is computed on top (app/dashboard/funnel.py).
#
# UTM tags on metric_points are the JOIN KEY to artifacts.utm_*, so where
# a row carries them we attribute performance back to the content the
# system produced. Untagged rows (typical for historical baseline data)
# stay backdrop — never claimed as something the system drove.
# ---------------------------------------------------------------------------
class ReportUpload(Base, TimestampMixin):
    """One uploaded report file. `mode` distinguishes the historical
    backdrop ("baseline") from recent rows we'll try to attribute
    ("ongoing"). column_mapping records how columns were resolved so a
    later debug or re-import has full traceability."""
    __tablename__ = "report_uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    # User-supplied label of which tool produced the file (e.g. "ga",
    # "semrush", "linkedin_ads", "manual"). Stored on every metric_point
    # in the same upload so we can filter or trace by tool.
    source: Mapped[str] = mapped_column(String(50), default="manual")
    # "baseline" → backdrop only, never attributed.
    # "ongoing"  → attributed where UTM/campaign matches produced content.
    mode: Mapped[str] = mapped_column(String(20), default="ongoing")
    column_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # OPTIONAL product scope: when the upload is recognized as belonging to
    # a specific product (e.g. the user uploaded "SimpleLegal CLM" perf
    # rows under that product), this flags it for downstream filtering.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True)


class MetricPoint(Base, TimestampMixin):
    """One normalized observation. The schema is the generic point; the
    VIEW is curated (top/middle/bottom funnel stages computed at read
    time). Storing generically means a new tool's export just needs a
    column-mapping — no schema migration."""
    __tablename__ = "metric_points"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    report_upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("report_uploads.id"), nullable=True, index=True)
    # Which tool exported this row (denormalized from ReportUpload.source
    # for cheap filtering without a join).
    source: Mapped[str] = mapped_column(String(50), default="manual")
    metric_name: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    date: Mapped[datetime] = mapped_column(Date, index=True)
    # Optional dimension (channel, page, keyword, account, ...).
    segment: Mapped[str] = mapped_column(String(200), default="")
    # The join key to artifacts.utm_*. Present only when the report
    # actually carried UTMs (typical for "ongoing" uploads — historical
    # baseline rows usually arrive untagged).
    utm_campaign: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    utm_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(60), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # "Where this row came from" for debug/audit (row index, raw values,
    # auto-mapping decisions). Not load-bearing — never read by views.
    raw_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    # True when this point came from a "baseline" upload — backdrop only,
    # never attributed to produced content. Mirrors the honesty discipline
    # used for csv vs csv+intent in the market_intel ingest.
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # OPTIONAL product scope. Inherited from the parent ReportUpload at
    # ingest time so the dashboard can filter the funnel to a product.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True)


class Suggestion(Base, TimestampMixin):
    """An evidence-attached recommendation the cockpit surfaces. Two kinds:
       * "trend"    — grounded in the org's OWN funnel + production data.
                      Deterministic rule-based generators always produce
                      these (no LLM dependency), and every one carries the
                      specific data motivating it.
       * "industry" — the model's general read of the space. EXPLICITLY
                      labeled as perspective; carries confabulation risk
                      and is never presented as current data.
    `idea_*` fields capture a one-click route into the content engine —
    when set, the UI offers "Generate this" which POSTs an /api/runs
    content_engine task pre-filled with that idea."""
    __tablename__ = "suggestions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    recommendation: Mapped[str] = mapped_column(Text)
    # Specific data points / numbers the recommendation rests on. Required:
    # a suggestion without its "because" is not allowed (see the brief).
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    # UI-ready label: "Trend-based — grounded in your data" |
    # "General industry perspective — verify before acting".
    source_label: Mapped[str] = mapped_column(String(120), default="")
    idea_content_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    idea_topic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    idea_target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|dismissed|actioned
    # OPTIONAL product scope: suggestions computed from a product-filtered
    # funnel carry this so the cockpit can show "X suggestions for SimpleLegal".
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True)


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
