from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import AgentRegistration, Artifact, Run, Upload, User
from app.queue import enqueue
from app.schemas import RunOut, TriggerRunIn
from app.tenancy import scoped

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("", response_model=RunOut)
def trigger_run(body: TriggerRunIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    reg = db.execute(
        scoped(AgentRegistration, user.org_id).where(AgentRegistration.key == body.agent_key)
    ).scalar_one_or_none()
    if reg is None or not reg.enabled:
        raise HTTPException(404, f"Agent '{body.agent_key}' not enabled for this org")

    # If the caller bound this run to an uploaded list, the upload MUST belong
    # to their org. scoped() is the only safe way to check — never trust a
    # client-supplied id without the tenant filter.
    upload_id: str | None = None
    if body.upload_id:
        up = db.execute(
            scoped(Upload, user.org_id).where(Upload.id == body.upload_id)
        ).scalar_one_or_none()
        if up is None:
            raise HTTPException(404, f"Upload '{body.upload_id}' not found for this org")
        upload_id = up.id

    # Persist the caller's per-run task on the Run row so the worker can hand
    # it to the agent through ctx.task. Closes the previous "task is dropped"
    # gap — the content_engine relies on this for action/content_type/topic.
    run = Run(org_id=user.org_id, agent_registration_id=reg.id, agent_key=reg.key,
              trigger="manual", status="queued", created_by=user.id,
              upload_id=upload_id, task=body.task or {})
    db.add(run)
    db.commit()
    db.refresh(run)
    enqueue(db, user.org_id, "run_agent", {"run_id": run.id})
    return RunOut.of(run)


@router.get("", response_model=list[RunOut])
def list_runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    runs = db.execute(scoped(Run, user.org_id).order_by(Run.created_at.desc())).scalars().all()
    return [RunOut.of(r) for r in runs]


@router.get("/{run_id}")
def get_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.execute(scoped(Run, user.org_id).where(Run.id == run_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    artifacts = db.execute(
        scoped(Artifact, user.org_id).where(Artifact.run_id == run_id)
    ).scalars().all()
    return {
        "run": RunOut.of(run).model_dump(),
        "logs": run.logs,
        "artifacts": [
            {"id": a.id, "type": a.type, "title": a.title, "body": a.body,
             "citations": a.citations, "status": a.status} for a in artifacts
        ],
    }
