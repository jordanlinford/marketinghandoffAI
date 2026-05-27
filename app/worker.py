"""
The worker. Polls the job queue, runs one agent per job inside a tenant-scoped
AgentContext, and persists everything (artifacts, gated proposals, cost, logs,
audit trail). This is where the five usability fundamentals live: observability,
idempotency, guardrails, cost capture, and graceful failure.

Run as a separate process:  python -m app.worker
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app import guardrails
from app.agents.registry import get_agent
from app.config import get_settings
from app.data_sources.csv import CsvMarketDataSource
from app.data_sources.stub import StubMarketDataSource
from app.db import SessionLocal
from app.models import (AgentRegistration, Artifact, AuditLog, Guardrail, Org,
                        OrgProfile, Proposal, Run, Upload, _now)
from app.products import resolve_product_profile
from app.queue import complete, lease_next
from app.schemas import AgentContext
from app.tenancy import assert_same_org, scoped


def _resolve_market_data(db: Session, org_id: str, upload_id: str | None):
    """Pick a data source for this run.
      * upload_id set → CsvMarketDataSource(uploaded rows) — the real-accounts path.
      * upload_id None → StubMarketDataSource — unchanged from P0.
    Later: inspect connections and return ZoomInfoDataSource / ApolloDataSource.
    Same interface either way; the agent does not change."""
    if upload_id:
        up = db.execute(
            scoped(Upload, org_id).where(Upload.id == upload_id)
        ).scalar_one_or_none()
        if up is None:
            raise RuntimeError(f"Upload {upload_id} not found for org {org_id}")
        return CsvMarketDataSource(up.rows)
    return StubMarketDataSource()


def _serialize_artifact(art: Artifact) -> dict:
    """Stable view the agent sees in ctx.prior_artifacts. Mirrors the columns
    the agent might read (notably parent_id + utm_* for the regenerate path)
    without exposing the ORM row."""
    return {
        "id": art.id, "type": art.type, "title": art.title,
        "body": art.body or {}, "citations": art.citations or [],
        "status": art.status,
        "parent_id": art.parent_id,
        "grade": art.grade,
        "utm_campaign": art.utm_campaign, "utm_source": art.utm_source,
        "utm_medium": art.utm_medium, "utm_content": art.utm_content,
        "destination_url": art.destination_url,
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }


def _latest_market_brief(db: Session, org_id: str) -> dict | None:
    """Latest succeeded market_brief artifact for this org (via scoped()), so
    the content_engine can ground its suggestions in real market data. Returns
    None when none exists. Read by agents through ctx.prior_artifacts — they
    never query the DB."""
    art = db.execute(
        scoped(Artifact, org_id).where(Artifact.type == "market_brief")
        .order_by(Artifact.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if art is None:
        return None
    return _serialize_artifact(art)


def _parent_artifact(db: Session, org_id: str,
                     parent_artifact_id: str | None) -> dict | None:
    """Load a specific prior artifact by id via scoped() — used by the
    content_engine's "Give me something better" path. Returns None when the
    id is absent, missing, or belongs to a different org. Cross-tenant safety
    is enforced here so the agent never sees another org's artifact even if
    a client supplies a guessed id."""
    if not parent_artifact_id:
        return None
    art = db.execute(
        scoped(Artifact, org_id).where(Artifact.id == parent_artifact_id)
    ).scalar_one_or_none()
    if art is None:
        return None
    return _serialize_artifact(art)


def _build_guardrail_rules(db: Session, org_id: str,
                           profile: dict | None) -> dict[str, dict]:
    """Build the rules-by-scope dict the agent sees through ctx.guardrail_rules
    (and that this worker uses to gate proposals). Merges OrgProfile.banned_claims
    into the "content" scope so the profile stays the single source of truth —
    edit the profile, the content guardrail follows."""
    rules: dict[str, dict] = {
        g.scope: dict(g.rules or {})
        for g in db.execute(scoped(Guardrail, org_id)).scalars()
    }
    if profile and profile.get("banned_claims"):
        content_rules = rules.setdefault("content", {})
        content_rules["banned_claims"] = list(profile["banned_claims"])
    return rules


def _confirmed_org_profile(db: Session, org_id: str) -> dict | None:
    """Load the org's CONFIRMED OrgProfile (via scoped()) as the plain dict the
    agent reads through ctx.org_profile. Returns None when no confirmed profile
    exists, so the agent falls back to its seed config (no regression). A draft
    (confirmed=false) is intentionally NOT passed — only the user's saved truth
    drives a run."""
    prof = db.execute(scoped(OrgProfile, org_id)).scalar_one_or_none()
    if prof is None or not prof.confirmed:
        return None
    return {
        "product_summary": prof.product_summary,
        "value_prop": prof.value_prop,
        "icp": prof.icp or {},
        "competitors": prof.competitors or [],
        "keywords": prof.keywords or [],
        "brand_voice": prof.brand_voice,
        "banned_claims": prof.banned_claims or [],
        "conversion_goal": prof.conversion_goal,
        "website_url": prof.website_url,
        "content_review_mode": prof.content_review_mode or "guardrail",
    }


def _audit(db: Session, org_id: str, actor: str, action: str, target_type: str,
           target_id: str, meta: dict | None = None) -> None:
    db.add(AuditLog(org_id=org_id, actor=actor, action=action,
                    target_type=target_type, target_id=target_id, meta=meta or {}))


def process_run(db: Session, run: Run) -> None:
    reg = db.get(AgentRegistration, run.agent_registration_id)
    if reg is None or not reg.enabled:
        raise RuntimeError("Agent registration missing or disabled")
    # Belt-and-suspenders: PK lookup, so assert org match before we trust it.
    assert_same_org(reg, run.org_id)

    run.status = "running"
    run.started_at = _now()
    db.commit()

    org = db.get(Org, run.org_id)
    org_name = org.name if org else run.org_id
    logs: list[str] = []
    org_profile = _confirmed_org_profile(db, run.org_id)
    # Resolved profile = org + (optional) product layer + per-field
    # provenance. This is the canonical input the agent reads through
    # ctx.profile. With no product selected on the run, it's effectively
    # the org-level view (backwards-compatible).
    resolved_profile = resolve_product_profile(db, run.org_id, run.product_id)
    rules_by_scope = _build_guardrail_rules(db, run.org_id, org_profile)
    # Per-run task: prefer the explicit one persisted on the Run row (set by
    # the API caller), fall back to the agent's default_task from its config.
    task = run.task if run.task else reg.config.get("default_task", {})
    ctx = AgentContext(
        org_id=run.org_id,
        org_name=org_name,
        agent_key=run.agent_key,
        registration_id=reg.id,
        run_id=run.id,
        trigger=run.trigger,
        task=task,
        brand_guide=reg.config.get("brand_guide", {}),
        icp=reg.config.get("icp", {}),
        config=reg.config,
        org_profile=org_profile,
        profile=resolved_profile,
        product_id=run.product_id,
        # The agent reads "what else exists for this org" only through this
        # list. Brief always; parent draft only when the task names one (the
        # regenerate path). Both are loaded via scoped() so cross-tenant ids
        # can't sneak through, even if a client guesses one.
        prior_artifacts=[a for a in (
            _parent_artifact(db, run.org_id,
                             (run.task or {}).get("parent_artifact_id")),
            _latest_market_brief(db, run.org_id),
        ) if a],
        guardrail_rules=rules_by_scope,
        get_market_data=lambda: _resolve_market_data(db, run.org_id, run.upload_id),
        log=logs.append,
    )

    agent = get_agent(run.agent_key)        # builtin resolution (http/mcp kinds: P3)
    result = agent.run(ctx)

    # Persist artifacts (with status — defaults to "ready", content_engine may
    # set "pending_review" for drafts that need queue review).
    artifact_rows: list[Artifact] = []
    for a in result.artifacts:
        row = Artifact(
            org_id=run.org_id, run_id=run.id,
            # Stamp the product on every artifact so the dashboard can
            # filter by product without re-joining through Run.
            product_id=run.product_id,
            type=a.type, title=a.title,
            body=a.body, citations=[c.model_dump() for c in a.citations],
            status=getattr(a, "status", "ready"),
            # New content-engine columns (None for non-content artifacts).
            parent_id=getattr(a, "parent_id", None),
            grade=getattr(a, "grade", None),
            utm_campaign=getattr(a, "utm_campaign", None),
            utm_source=getattr(a, "utm_source", None),
            utm_medium=getattr(a, "utm_medium", None),
            utm_content=getattr(a, "utm_content", None),
            destination_url=getattr(a, "destination_url", None),
        )
        db.add(row)
        artifact_rows.append(row)
    # Flush so the new artifact rows have ids the proposals can reference.
    db.flush()

    # Persist proposals, each gated before it could ever execute.
    for p in result.proposed_actions:
        status, detail = guardrails.evaluate(p, rules_by_scope)
        db.add(Proposal(
            org_id=run.org_id, run_id=run.id,
            product_id=run.product_id,
            action_type=p.action_type, payload=p.payload,
            guardrail_scope=p.guardrail_scope, guardrail_status=status, guardrail_detail=detail,
            reasoning=p.reasoning, status="pending",
        ))

    run.status = "succeeded"
    run.cost_usd = result.cost_usd
    run.finished_at = _now()
    run.logs = logs + result.logs
    _audit(db, run.org_id, "system", "run.succeeded", "run", run.id,
           {"agent": run.agent_key, "artifacts": len(result.artifacts),
            "proposals": len(result.proposed_actions), "cost_usd": result.cost_usd})
    db.commit()


def run_once(db: Session | None = None) -> bool:
    """Process at most one job. Returns True if a job was handled."""
    own = db is None
    db = db or SessionLocal()
    try:
        job = lease_next(db)
        if job is None:
            return False
        run = db.get(Run, job.payload.get("run_id"))
        try:
            if run is None:
                raise RuntimeError(f"Run {job.payload.get('run_id')} not found")
            # PK lookup; verify the job and run agree on tenant before any work.
            assert_same_org(run, job.org_id)
            process_run(db, run)
            complete(db, job)
        except Exception as exc:  # graceful failure: mark run + job, never crash loop
            db.rollback()
            if run is not None:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = _now()
                _audit(db, run.org_id, "system", "run.failed", "run", run.id, {"error": str(exc)})
                db.commit()
            complete(db, job, error=str(exc))
        return True
    finally:
        if own:
            db.close()


def run_forever() -> None:
    settings = get_settings()
    print(f"[worker] polling every {settings.worker_poll_seconds}s "
          f"(env={settings.app_env}, db={'sqlite' if settings.is_sqlite else 'postgres'})")
    while True:
        worked = run_once()
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
