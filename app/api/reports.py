"""
Reports API — the storytelling layer's public surface.

Three endpoints, all scoped via current_user:
  * POST /api/reports/intelligence — synchronous preview. Runs the
    deterministic engine and returns the structured intelligence
    object the report will be based on. No LLM, no run, no worker.
    Useful so the UI can show "what data is this report going to
    draw from?" before incurring a paid generation.
  * POST /api/reports/generate — enqueues a Run for report_composer
    with the audience + scope on the task. The worker pre-computes
    intelligence and dispatches to the audience-specific renderer.
    Returns the run id so the UI can poll.
  * GET /api/reports — lists generated reports for the org. Thin
    projection over Artifacts with content_type=report_*.

Reports are first-class Artifacts (type=content_draft) — they inherit
the Library, .md download, grading, and approval queue paths from
the existing content surfaces.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import AgentRegistration, Artifact, Run, User
from app.queue import enqueue
from app.reports import build_report_intelligence
from app.reports.renderers import RENDERERS
from app.tenancy import scoped

router = APIRouter(prefix="/api/reports", tags=["reports"])


_REPORT_CONTENT_TYPES = {
    "report_board", "report_ceo_weekly", "report_sales_leadership",
}


class ScopeIn(BaseModel):
    kind: str = Field(..., description="'time_window' or 'campaign'")
    start: str | None = None
    end: str | None = None
    campaign_id: str | None = None


class IntelligenceIn(BaseModel):
    scope: ScopeIn
    product_id: str | None = None
    lookback_days: int = 30


class GenerateIn(BaseModel):
    # Audience drives the renderer dispatch. Reports
    # (board / ceo_weekly / sales_leadership) and anchor classes
    # (whitepaper today; buyer_guide / solution_guide later) all
    # co-register in app.reports.renderers.RENDERERS, and the agent
    # promotes anchors to their own Artifact.type — same kind-
    # promotion mechanism as the original report kind.
    audience: str = Field(...,
        description="'board' | 'ceo_weekly' | 'sales_leadership' | 'whitepaper'")
    scope: ScopeIn
    product_id: str | None = None
    lookback_days: int = 30
    parent_artifact_id: str | None = None
    critique: str = ""


def _validate_audience(audience: str) -> str:
    a = (audience or "").strip().lower()
    if a not in RENDERERS:
        raise HTTPException(
            400, f"audience must be one of {sorted(RENDERERS.keys())}")
    return a


def _scope_dict(scope: ScopeIn) -> dict:
    out: dict[str, Any] = {"kind": scope.kind}
    if scope.start:
        out["start"] = scope.start
    if scope.end:
        out["end"] = scope.end
    if scope.campaign_id:
        out["campaign_id"] = scope.campaign_id
    return out


# --------------------------------------------------------------------------
# POST /api/reports/intelligence — synchronous preview
# --------------------------------------------------------------------------
@router.post("/intelligence")
def preview_intelligence(body: IntelligenceIn,
                         user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    intelligence = build_report_intelligence(
        db, user.org_id,
        product_id=body.product_id,
        scope=_scope_dict(body.scope),
        lookback_days=body.lookback_days,
    )
    return intelligence


# --------------------------------------------------------------------------
# POST /api/reports/generate — async via worker
# --------------------------------------------------------------------------
@router.post("/generate")
def generate_report(body: GenerateIn,
                    user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    audience = _validate_audience(body.audience)
    reg = db.execute(
        scoped(AgentRegistration, user.org_id)
        .where(AgentRegistration.key == "report_composer")
    ).scalar_one_or_none()
    if reg is None or not reg.enabled:
        raise HTTPException(
            404, "report_composer agent is not enabled for this org")
    task: dict[str, Any] = {
        "audience": audience,
        "scope": _scope_dict(body.scope),
        "lookback_days": body.lookback_days,
    }
    if body.parent_artifact_id:
        task["parent_artifact_id"] = body.parent_artifact_id
    if body.critique:
        task["critique"] = body.critique
    run = Run(
        org_id=user.org_id, agent_registration_id=reg.id,
        agent_key="report_composer", trigger="manual",
        status="queued", created_by=user.id,
        product_id=body.product_id, task=task,
    )
    db.add(run)
    db.flush()
    enqueue(db, user.org_id, "run_agent", {"run_id": run.id})
    db.commit()
    db.refresh(run)
    return {"run_id": run.id, "agent_key": run.agent_key,
            "audience": audience, "status": run.status}


# --------------------------------------------------------------------------
# GET /api/reports — list reports for the org
# --------------------------------------------------------------------------
@router.get("")
def list_reports(audience: str | None = None,
                 product_id: str | None = None,
                 status: str | None = None,
                 limit: int = 100,
                 user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> list[dict]:
    """Thin projection over Artifacts. The kind field (Artifact.type) is
    now "report_draft" for new reports; legacy reports that still carry
    type="content_draft" are picked up via the body.content.content_type
    check below until the backfill catches them on the next API
    startup."""
    q = scoped(Artifact, user.org_id).where(
        Artifact.type.in_(("report_draft", "content_draft"))
    ).order_by(Artifact.created_at.desc())
    if product_id:
        q = q.where(Artifact.product_id == product_id)
    if status:
        q = q.where(Artifact.status == status)
    rows = db.execute(q).scalars().all()
    out: list[dict] = []
    audience_filter = (audience or "").strip().lower()
    for a in rows:
        body = a.body or {}
        content = body.get("content") or {}
        ct = content.get("content_type") or ""
        if ct not in _REPORT_CONTENT_TYPES:
            continue
        audience_key = ct.removeprefix("report_") if ct else ""
        if audience_filter and audience_key != audience_filter:
            continue
        metadata = content.get("metadata") or {}
        scope_info = (metadata.get("scope")
                      or (body.get("provenance") or {}).get("intelligence_scope")
                      or {})
        out.append({
            "id": a.id,
            "content_type": ct,
            "audience": audience_key,
            "title": a.title,
            "status": a.status,
            "product_id": a.product_id,
            "scope": scope_info,
            "render_strategy": metadata.get("render_strategy"),
            "grade": (a.grade or {}).get("overall") if a.grade else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "parent_id": a.parent_id,
        })
        if len(out) >= limit:
            break
    return out
