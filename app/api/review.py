"""
The approval queue — the screen the team lives in. Everything upstream is
plumbing; this is the product. v1 exposes the queue + approve/reject with full
audit logging.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import Artifact, AuditLog, Proposal, User, _now
from app.schemas import ProposalDecisionIn
from app.tenancy import scoped

router = APIRouter(prefix="/api/review", tags=["review"])


def _serialize(p: Proposal) -> dict:
    return {
        "id": p.id, "run_id": p.run_id, "action_type": p.action_type,
        "payload": p.payload, "reasoning": p.reasoning,
        "guardrail_scope": p.guardrail_scope, "guardrail_status": p.guardrail_status,
        "guardrail_detail": p.guardrail_detail, "status": p.status,
        "created_at": p.created_at.isoformat(),
    }


@router.get("/proposals")
def list_proposals(status: str = "pending", user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    q = scoped(Proposal, user.org_id).where(Proposal.status == status) \
        .order_by(Proposal.created_at.desc())
    return [_serialize(p) for p in db.execute(q).scalars().all()]


def _decide(db: Session, user: User, proposal_id: str, decision: str, note: str):
    p = db.execute(
        scoped(Proposal, user.org_id).where(Proposal.id == proposal_id)
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Proposal not found")
    if p.status != "pending":
        raise HTTPException(409, f"Proposal already {p.status}")
    p.status = decision
    p.approver_id = user.id
    p.decided_at = _now()
    # For content_review proposals, mirror the decision onto the linked
    # content_draft artifact's status — "ready" on approve, "rejected" on
    # reject. The artifact shares the proposal's run_id (the worker sets both
    # from the same Run), so scoped() by run_id is the safe linkage. NOTE:
    # "ready" never means "published" — publishing is a separate, always-
    # gated action that is out of scope for v1.
    if p.action_type == "content_review":
        art = db.execute(
            scoped(Artifact, user.org_id)
            .where(Artifact.run_id == p.run_id, Artifact.type == "content_draft")
        ).scalar_one_or_none()
        if art is not None:
            art.status = "ready" if decision == "approved" else "rejected"
    db.add(AuditLog(org_id=user.org_id, actor=user.id, action=f"proposal.{decision}",
                    target_type="proposal", target_id=p.id,
                    meta={"note": note, "action_type": p.action_type}))
    db.commit()
    return _serialize(p)


@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: str, body: ProposalDecisionIn, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    return _decide(db, user, proposal_id, "approved", body.note)


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: str, body: ProposalDecisionIn, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    return _decide(db, user, proposal_id, "rejected", body.note)
