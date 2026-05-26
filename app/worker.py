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
    return {
        "id": art.id, "type": art.type, "title": art.title,
        "body": art.body or {}, "citations": art.citations or [],
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }


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
    profile = _confirmed_org_profile(db, run.org_id)
    rules_by_scope = _build_guardrail_rules(db, run.org_id, profile)
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
        org_profile=profile,
        prior_artifacts=[b for b in [_latest_market_brief(db, run.org_id)] if b],
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
        row = Artifact(org_id=run.org_id, run_id=run.id, type=a.type, title=a.title,
                       body=a.body, citations=[c.model_dump() for c in a.citations],
                       status=getattr(a, "status", "ready"))
        db.add(row)
        artifact_rows.append(row)
    # Flush so the new artifact rows have ids the proposals can reference.
    db.flush()

    # Persist proposals, each gated before it could ever execute.
    for p in result.proposed_actions:
        status, detail = guardrails.evaluate(p, rules_by_scope)
        db.add(Proposal(
            org_id=run.org_id, run_id=run.id, action_type=p.action_type, payload=p.payload,
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
