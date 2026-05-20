"""
Postgres/SQLite-backed job queue. Deliberately boring: a `jobs` table the worker
polls. Robust and debuggable for a team-sized load.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Job, _now


def enqueue(db: Session, org_id: str, kind: str, payload: dict) -> Job:
    job = Job(org_id=org_id, kind=kind, payload=payload, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def lease_next(db: Session, lease_seconds: int = 120) -> Job | None:
    """Atomically claim the next queued OR expired-leased job. SKIP LOCKED on
    Postgres so workers never collide; on SQLite (single worker) the immediate
    transaction is sufficient. Including expired leases means a worker that
    died mid-job won't strand that job — the next worker reclaims it.

    Note: this is NOT a per-tenant query (the queue is a system-level
    resource). Tenant verification happens in the worker before any work runs
    (worker.run_once asserts run.org_id == job.org_id)."""
    settings = get_settings()
    now = _now()
    stmt = (
        select(Job)
        .where(
            or_(
                Job.status == "queued",
                (Job.status == "leased") & (Job.lease_until < now),
            )
        )
        .order_by(Job.created_at)
        .limit(1)
    )
    if not settings.is_sqlite:
        stmt = stmt.with_for_update(skip_locked=True)

    job = db.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    job.status = "leased"
    job.attempts += 1
    job.lease_until = now + timedelta(seconds=lease_seconds)
    db.commit()
    db.refresh(job)
    return job


def complete(db: Session, job: Job, error: str | None = None) -> None:
    job.status = "error" if error else "done"
    job.last_error = error
    db.commit()
