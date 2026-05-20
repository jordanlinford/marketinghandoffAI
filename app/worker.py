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
from app.data_sources.stub import StubMarketDataSource
from app.db import SessionLocal
from app.models import AgentRegistration, Artifact, AuditLog, Guardrail, Org, Proposal, Run, _now
from app.queue import complete, lease_next
from app.schemas import AgentContext
from app.tenancy import assert_same_org, scoped


def _resolve_market_data(db: Session, org_id: str):
    """Pick a data source for this org. Stub today; later, inspect connections
    and return ZoomInfoDataSource / ApolloDataSource. Same interface either way."""
    return StubMarketDataSource()


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
    ctx = AgentContext(
        org_id=run.org_id,
        org_name=org_name,
        agent_key=run.agent_key,
        registration_id=reg.id,
        run_id=run.id,
        trigger=run.trigger,
        task=reg.config.get("default_task", {}),
        brand_guide=reg.config.get("brand_guide", {}),
        icp=reg.config.get("icp", {}),
        config=reg.config,
        get_market_data=lambda: _resolve_market_data(db, run.org_id),
        log=logs.append,
    )

    agent = get_agent(run.agent_key)        # builtin resolution (http/mcp kinds: P3)
    result = agent.run(ctx)

    # Persist artifacts
    for a in result.artifacts:
        db.add(Artifact(org_id=run.org_id, run_id=run.id, type=a.type, title=a.title,
                        body=a.body, citations=[c.model_dump() for c in a.citations]))

    # Persist proposals, each gated before it could ever execute
    rules_by_scope = {
        g.scope: g.rules
        for g in db.execute(scoped(Guardrail, run.org_id)).scalars()
    }
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
