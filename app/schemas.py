"""
The agent contract.

This is the seam that keeps the platform extensible. Every agent — whether a
first-party Python class today, or a teammate's HTTP/MCP endpoint tomorrow —
takes an AgentContext and returns an AgentResult. Lock this schema down and you
never have to retrofit "bring your own agent."

  AgentContext  : runtime, in-memory, rich (live data-source handles, logger).
                  A dataclass, not persisted.
  AgentResult   : serializable output, persisted as Run + Artifacts + Proposals.
                  Pydantic, so it round-trips cleanly to/from JSON (and over
                  HTTP to a remote agent).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field


# ---- Serializable output (the contract's return type) ---------------------
class Citation(BaseModel):
    source: str
    url: str | None = None
    snippet: str | None = None


class ArtifactDraft(BaseModel):
    type: str
    title: str
    body: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    # Review status of the artifact when it lands in the DB. Defaults to
    # "ready" (the existing market_intel path), so older agents keep working.
    # The content_engine sets "pending_review" when a draft is routed to the
    # approval queue; the queue flips it to "ready" or "rejected" on decision.
    status: str = "ready"


class ProposedAction(BaseModel):
    """A side-effect the agent wants to take. Never executed without a gate."""
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    guardrail_scope: str | None = None


class AgentResult(BaseModel):
    artifacts: list[ArtifactDraft] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    cost_usd: float = 0.0
    logs: list[str] = Field(default_factory=list)


# ---- Runtime input (rich; not persisted) ----------------------------------
@dataclass
class AgentContext:
    org_id: str
    org_name: str
    agent_key: str
    registration_id: str
    run_id: str
    trigger: str = "manual"
    task: dict[str, Any] = field(default_factory=dict)
    brand_guide: dict[str, Any] = field(default_factory=dict)
    icp: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    # The org's CONFIRMED OrgProfile (ICP, product, competitors, keywords),
    # loaded by the worker via scoped() and handed in as a plain dict — the
    # agent never touches the DB. None when no confirmed profile exists, in
    # which case agents fall back to seed config. In-agent precedence is
    # explicit: confirmed org_profile > seed config.
    org_profile: dict[str, Any] | None = None
    # Recently-completed artifacts for this org that an agent may reason from
    # (e.g. content_engine reads the latest market_brief). Loaded by the
    # worker via scoped(); the agent never queries the DB.
    prior_artifacts: list[dict[str, Any]] = field(default_factory=list)
    # The org's guardrail rules indexed by scope (spend / publish / content /
    # ...). The worker loads these via scoped() and merges in dynamic profile
    # data (e.g. banned_claims). Agents call app.guardrails.evaluate() against
    # this dict — they do not read the Guardrail table directly.
    guardrail_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    get_market_data: Callable[[], Any] | None = None
    log: Callable[[str], None] = lambda msg: None


# ---- API DTOs --------------------------------------------------------------
class TriggerRunIn(BaseModel):
    agent_key: str
    task: dict[str, Any] = Field(default_factory=dict)
    # Optional: bind this run to a previously uploaded account list. Validated
    # against the caller's org via scoped() before the run is created.
    upload_id: str | None = None


class RunOut(BaseModel):
    id: str
    agent_key: str
    status: str
    trigger: str
    cost_usd: float
    error: str | None = None
    upload_id: str | None = None
    created_at: str

    @classmethod
    def of(cls, r) -> "RunOut":
        return cls(
            id=r.id, agent_key=r.agent_key, status=r.status, trigger=r.trigger,
            cost_usd=r.cost_usd, error=r.error, upload_id=r.upload_id,
            created_at=r.created_at.isoformat(),
        )


class ProposalDecisionIn(BaseModel):
    note: str = ""
