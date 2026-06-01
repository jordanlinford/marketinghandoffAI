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
    # Optional artifact-column fields the content_engine uses; the worker
    # copies them onto the Artifact row. None for non-content artifacts.
    parent_id: str | None = None
    grade: dict[str, Any] | None = None
    utm_campaign: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_content: str | None = None
    destination_url: str | None = None


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
    # The ResolvedProfile from app/products.resolve_product_profile — the
    # ONLY way agents should read profile-level config. With no product
    # selected this is effectively the org-level view; with a product set
    # it carries inherited fields, product-only fields under
    # profile["product"], and per-field provenance. ctx.org_profile stays
    # for backwards compat but agents on the new path read ctx.profile.
    profile: dict[str, Any] | None = None
    product_id: str | None = None
    # Recently-completed artifacts for this org that an agent may reason from
    # (e.g. content_engine reads the latest market_brief). Loaded by the
    # worker via scoped(); the agent never queries the DB.
    prior_artifacts: list[dict[str, Any]] = field(default_factory=list)
    # The org's guardrail rules indexed by scope (spend / publish / content /
    # ...). The worker loads these via scoped() and merges in dynamic profile
    # data (e.g. banned_claims). Agents call app.guardrails.evaluate() against
    # this dict — they do not read the Guardrail table directly.
    guardrail_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Persistent marketing memory — list of Pattern dicts pre-loaded by
    # the worker via app.memory.query_memory(). The agent never queries
    # the DB; if it needs memory it READS it from ctx, same contract as
    # prior_artifacts + guardrail_rules. Empty list = no telemetry yet
    # (backwards compat: agents behave exactly as they always have).
    memory_patterns: list[dict[str, Any]] = field(default_factory=list)
    # Pre-computed ReportIntelligence object for report_composer runs.
    # Like memory_patterns, this is loaded by the worker (which has the
    # db session) and handed to the agent — agents never touch the DB.
    # None for non-report runs.
    report_intelligence: dict[str, Any] | None = None
    # Tenant brand identity — PRESENTATION ONLY. Brand tokens feed
    # chrome (logo, colors, fonts). They MUST NOT enter the
    # intelligence object, the evidence ledger, or any input a §6/§7
    # validator reads. Renderers treat this field as a verbatim
    # pass-through; never as a signal that shapes selection,
    # prompt content, or claim wording. Always present (defaults
    # apply when no row exists) — unset is not an error.
    brand: dict[str, Any] = field(default_factory=dict)
    get_market_data: Callable[[], Any] | None = None
    log: Callable[[str], None] = lambda msg: None


# ---- API DTOs --------------------------------------------------------------
class TriggerRunIn(BaseModel):
    agent_key: str
    task: dict[str, Any] = Field(default_factory=dict)
    # Optional: bind this run to a previously uploaded account list. Validated
    # against the caller's org via scoped() before the run is created.
    upload_id: str | None = None
    # Optional: scope this run to a product (child of the org). NULL keeps
    # the legacy org-level behavior — the runs.py endpoint validates the
    # product id belongs to the caller's org via scoped() before persisting.
    product_id: str | None = None


class RunOut(BaseModel):
    id: str
    agent_key: str
    status: str
    trigger: str
    cost_usd: float
    error: str | None = None
    upload_id: str | None = None
    product_id: str | None = None
    created_at: str
    # display_label — surface enough identity to distinguish reports
    # from one another in HQ Recent Runs + Create run list. For
    # report_composer runs, we surface audience + scope hint; other
    # agents render their agent_key as before. Pulled from task (no
    # new data — just shaped for the UI).
    display_label: str | None = None
    # report_trust_state — for report_composer runs that produced a
    # report artifact, this carries the SAME trust state the library
    # list pill and the detail banner show. Single source via
    # `compact_trust_state(body.trust_checks)` — never recomputed.
    # Populated by list_runs at projection time; None for non-report
    # runs and for report runs whose artifact hasn't landed yet.
    report_trust_state: str | None = None

    @classmethod
    def of(cls, r, *, report_trust_state: str | None = None) -> "RunOut":
        task = r.task or {}
        display_label = None
        if r.agent_key == "report_composer":
            audience_raw = (task.get("audience") or "").strip().lower()
            audience = audience_raw.replace("_", " ")
            scope = task.get("scope") or {}
            kind = scope.get("kind")
            if kind == "time_window":
                hint = f"{scope.get('start') or '?'} → {scope.get('end') or '?'}"
            elif kind == "campaign":
                hint = "campaign"
            else:
                hint = ""
            audience_label = audience.title() if audience else "report"
            # Anchor classes (whitepaper today; buyer_guide / solution_guide
            # later) carry their own noun rather than reading as "Whitepaper
            # report". Audience reports keep the "<audience> report" framing.
            _ANCHOR_NOUN = {
                "whitepaper": "Whitepaper",
            }
            if audience_raw in _ANCHOR_NOUN:
                display_label = _ANCHOR_NOUN[audience_raw] + (
                    f" — {hint}" if hint else "")
            else:
                display_label = (f"{audience_label} report"
                                  + (f" — {hint}" if hint else ""))
        return cls(
            id=r.id, agent_key=r.agent_key, status=r.status, trigger=r.trigger,
            cost_usd=r.cost_usd, error=r.error, upload_id=r.upload_id,
            product_id=r.product_id,
            created_at=r.created_at.isoformat(),
            display_label=display_label,
            report_trust_state=report_trust_state,
        )


class ProposalDecisionIn(BaseModel):
    note: str = ""
