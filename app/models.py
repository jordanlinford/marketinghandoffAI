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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[list] = mapped_column(JSON, default=list)

    artifacts: Mapped[list["Artifact"]] = relationship(backref="run")
    proposals: Mapped[list["Proposal"]] = relationship(backref="run")


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    citations: Mapped[list] = mapped_column(JSON, default=list)


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
