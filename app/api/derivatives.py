"""
Derivatives API — public surface for Level-4 derivative generation.

One endpoint:
  * POST /api/derivatives/generate — enqueues a Run for
    derivative_composer with task = {derivative_type, source_anchor_id,
    ...}. The worker pre-loads the source anchor via scoped() (tenant
    isolation enforced at fetch time — an anchor id from another org
    will simply not resolve) and dispatches to the renderer in
    DERIVATIVE_RENDERERS.

Derivatives surface under /api/assets (the unified Library surface),
just like anchors — same projection, same trust pill, same detail
view. There is intentionally no parallel /api/derivatives listing
endpoint; the Library is the single source.

Listing-by-anchor (e.g. "show me every derivative of THIS anchor") is
out of scope for this build — minimal lineage is `body.source_anchor_id`
on the derivative artifact; consumers that need a derivative list per
anchor can query /api/assets and filter client-side.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.assets import _ANCHOR_TYPE_SET
from app.auth import current_user
from app.db import get_db
from app.documents.storage import storage_root
from app.models import AgentRegistration, Artifact, Run, User
from app.queue import enqueue
from app.reports.derivatives import DERIVATIVE_RENDERERS
from app.tenancy import scoped

router = APIRouter(prefix="/api/derivatives", tags=["derivatives"])


class GenerateDerivativeIn(BaseModel):
    derivative_type: str = Field(
        ...,
        description="One of the keys in DERIVATIVE_RENDERERS "
                     "(today: 'exec_summary').")
    source_anchor_id: str = Field(
        ...,
        description="Artifact id of the source anchor (any anchor "
                     "kind — report / whitepaper / buyer_guide / "
                     "solution_guide). Tenant isolation is enforced "
                     "at the worker via scoped().")
    parent_artifact_id: str | None = None
    critique: str = ""


def _validate_derivative_type(t: str) -> str:
    val = (t or "").strip().lower()
    if val not in DERIVATIVE_RENDERERS:
        raise HTTPException(
            400, f"derivative_type must be one of "
                 f"{sorted(DERIVATIVE_RENDERERS.keys())}")
    return val


@router.post("/generate")
def generate_derivative(body: GenerateDerivativeIn,
                        user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
    deriv_type = _validate_derivative_type(body.derivative_type)
    # Validate the source anchor belongs to the caller's org AND is an
    # anchor kind before enqueueing. This is a fail-fast — the worker
    # repeats the lookup via scoped() (defense in depth), but failing
    # the API call gives the caller an immediate 4xx rather than a
    # confusing async failure.
    anchor = db.execute(
        scoped(Artifact, user.org_id).where(
            Artifact.id == body.source_anchor_id,
            Artifact.type.in_(_ANCHOR_TYPE_SET),
        )
    ).scalar_one_or_none()
    if anchor is None:
        raise HTTPException(
            404, "source_anchor_id does not resolve to an anchor "
                 "artifact in this org. Confirm the id and that it "
                 "is a report / whitepaper / buyer_guide / "
                 "solution_guide.")
    reg = db.execute(
        scoped(AgentRegistration, user.org_id)
        .where(AgentRegistration.key == "derivative_composer")
    ).scalar_one_or_none()
    if reg is None or not reg.enabled:
        raise HTTPException(
            404, "derivative_composer agent is not enabled for this org")
    task: dict[str, Any] = {
        "derivative_type": deriv_type,
        "source_anchor_id": body.source_anchor_id,
    }
    if body.parent_artifact_id:
        task["parent_artifact_id"] = body.parent_artifact_id
    if body.critique:
        task["critique"] = body.critique
    run = Run(
        org_id=user.org_id, agent_registration_id=reg.id,
        agent_key="derivative_composer", trigger="manual",
        status="queued", created_by=user.id,
        product_id=anchor.product_id, task=task,
    )
    db.add(run)
    db.flush()
    enqueue(db, user.org_id, "run_agent", {"run_id": run.id})
    db.commit()
    db.refresh(run)
    return {"run_id": run.id, "agent_key": run.agent_key,
            "derivative_type": deriv_type,
            "source_anchor_id": body.source_anchor_id,
            "status": run.status}


@router.get("/{artifact_id}/slide/{idx}")
def serve_slide(artifact_id: str, idx: int,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> FileResponse:
    """Stream one carousel slide's rendered PNG. Tenant-scoped at
    the artifact lookup; the file path comes from
    body.rendered_assets[idx].path (RELATIVE to storage_root, so the
    URL can't escape into another org's directory)."""
    art = db.execute(
        scoped(Artifact, user.org_id).where(Artifact.id == artifact_id)
    ).scalar_one_or_none()
    if art is None:
        raise HTTPException(404, "Derivative artifact not found")
    assets = ((art.body or {}).get("rendered_assets")) or []
    record = next((a for a in assets
                    if isinstance(a, dict) and a.get("idx") == idx), None)
    if record is None:
        raise HTTPException(404, f"Slide idx={idx} not rendered for "
                                  "this artifact.")
    rel_path = record.get("path")
    if not rel_path:
        raise HTTPException(404, "Slide record missing path.")
    abs_path = storage_root() / rel_path
    if not abs_path.is_file():
        raise HTTPException(404, "Slide file missing on disk.")
    return FileResponse(path=str(abs_path), media_type="image/png",
                         filename=record.get("filename") or f"slide_{idx}.png")
