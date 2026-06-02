"""
Fan-out API — orchestrates one source anchor → N trust-bound
derivatives sharing a SELECTED lead claim.

THE GOVERNING DISCIPLINE this surface enforces (load-bearing):
the LEAD is a SELECTION — an ev:id pointer into the anchor's
existing ledger — NEVER a synthesized headline or unifying message
this layer authors. A fan-out where the API composes new prose for
the lead is the §6 fabrication §6 exists to prevent (propagated
into every sibling, it would pass §7 trivially while being a
fabricated claim none of the anchors made). See
app/reports/derivatives/leads.py for the SELECTION discipline.

Endpoints:
  * POST /api/fanouts/generate — validates lead + spawns N child
    derivative_composer runs. Returns the set id + per-child run
    ids. Children run independently through the existing §7
    containment gate; one blocked sibling does not block others.
  * GET  /api/fanouts/{set_id} — projects the set with each
    child's per-run trust state. Single source for trust per
    sibling (compact_trust_state on its produced artifact).

The set has NO trust_checks of its own. Trust lives on each child
artifact — same proven mechanism. The set is just orchestration.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.assets import _ANCHOR_TYPE_SET, _DERIVATIVE_TYPE_SET
from app.auth import current_user
from app.db import get_db
from app.models import (AgentRegistration, Artifact, FanoutSet, Run, User,
                         _uuid)
from app.queue import enqueue
from app.reports.derivatives import (DERIVATIVE_RENDERERS, select_lead,
                                       validate_lead)
from app.reports.trust_view import compact_trust_state
from app.tenancy import scoped

router = APIRouter(prefix="/api/fanouts", tags=["fanouts"])


class GenerateFanoutIn(BaseModel):
    source_anchor_id: str = Field(..., description=(
        "Artifact id of the source anchor (report / whitepaper / "
        "buyer_guide / solution_guide). Tenant isolation is enforced "
        "via scoped() at lookup time."))
    derivative_types: list[str] = Field(..., min_length=1,
        description=(
            "Which derivative kinds to spawn — values from "
            "DERIVATIVE_RENDERERS (today: exec_summary, carousel). "
            "One child run per type."))
    lead_ev_id: str | None = Field(None, description=(
        "Optional: override the auto-selected lead with a specific "
        "ledger id from the anchor. Must exist in the anchor's "
        "ledger AND be cited in the anchor's prose — otherwise the "
        "request fails. Omitting this triggers the deterministic "
        "select_lead() heuristic."))


def _validate_derivative_types(types: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in types:
        v = (t or "").strip().lower()
        if not v:
            continue
        if v not in DERIVATIVE_RENDERERS:
            raise HTTPException(
                400, f"derivative_type {v!r} not in registry "
                     f"{sorted(DERIVATIVE_RENDERERS.keys())}")
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    if not out:
        raise HTTPException(400, "derivative_types is empty after "
                                  "deduplication")
    return out


@router.post("/generate")
def generate_fanout(body: GenerateFanoutIn,
                    user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    derivative_types = _validate_derivative_types(body.derivative_types)
    # Fail-fast tenant-scoped anchor lookup. Same defense-in-depth as
    # /api/derivatives/generate — the API rejects up front; the worker
    # re-checks via scoped() when each child run begins.
    anchor = db.execute(
        scoped(Artifact, user.org_id).where(
            Artifact.id == body.source_anchor_id,
            Artifact.type.in_(_ANCHOR_TYPE_SET),
        )
    ).scalar_one_or_none()
    if anchor is None:
        raise HTTPException(
            404, "source_anchor_id does not resolve to an anchor "
                 "artifact in this org.")
    anchor_body = anchor.body or {}

    # ---- Lead resolution (SELECTION, not synthesis) -------------------
    # If the caller supplied an ev_id, it MUST exist in the anchor's
    # ledger AND be cited in anchor prose — both conditions enforced
    # by validate_lead. A lead that's a ledger entry but uncited in
    # prose isn't a claim the anchor MADE — it's just a number the
    # ledger happens to have. Foregrounding it would be a §6 sleight
    # of hand: presenting an unsurfaced number as the headline.
    if body.lead_ev_id:
        lead = validate_lead(anchor_body, body.lead_ev_id)
        if lead is None:
            raise HTTPException(
                400, f"lead_ev_id {body.lead_ev_id!r} does not "
                "resolve to a claim the source anchor makes. Leads "
                "must point at a ledger entry the anchor's prose "
                "cites (selection, not synthesis).")
    else:
        lead = select_lead(anchor_body)
        if lead is None:
            raise HTTPException(
                422, "Source anchor has no cited claims — there is "
                "nothing for the fan-out to foreground. Generate "
                "the anchor first; a fan-out without a real lead "
                "would have to invent one.")

    # ---- Find the derivative_composer registration -------------------
    reg = db.execute(
        scoped(AgentRegistration, user.org_id)
        .where(AgentRegistration.key == "derivative_composer")
    ).scalar_one_or_none()
    if reg is None or not reg.enabled:
        raise HTTPException(
            404, "derivative_composer agent is not enabled for this org")

    # ---- Create the FanoutSet row up front -------------------------
    # Child runs reference this id in their task — the set IS the
    # lineage anchor for the spawned runs.
    fanout_id = _uuid()
    fanout = FanoutSet(
        id=fanout_id,
        org_id=user.org_id,
        source_anchor_id=anchor.id,
        source_anchor_type=anchor.type,
        source_anchor_title=anchor.title,
        lead_ev_id=lead["ev_id"],
        lead_payload=lead,
        children=[],
        created_by=user.id,
    )
    db.add(fanout)

    # ---- Spawn one child run per derivative type --------------------
    # Each child is the EXISTING derivative_composer agent. The task
    # carries fanout_set_id (so the artifact body records membership)
    # + lead_ev_id (so the renderer foregrounds the right claim).
    # Each child runs independently — its trust gate is unchanged.
    children: list[dict] = []
    for d_type in derivative_types:
        run = Run(
            org_id=user.org_id,
            agent_registration_id=reg.id,
            agent_key="derivative_composer",
            trigger="manual",
            status="queued",
            created_by=user.id,
            product_id=anchor.product_id,
            task={
                "derivative_type": d_type,
                "source_anchor_id": anchor.id,
                "lead_ev_id": lead["ev_id"],
                "fanout_set_id": fanout_id,
            },
        )
        db.add(run)
        db.flush()
        enqueue(db, user.org_id, "run_agent", {"run_id": run.id})
        children.append({
            "derivative_type": d_type,
            "run_id": run.id,
        })

    fanout.children = children
    db.commit()
    db.refresh(fanout)

    return {
        "fanout_set_id": fanout.id,
        "source_anchor_id": fanout.source_anchor_id,
        "lead_ev_id": fanout.lead_ev_id,
        "lead_payload": fanout.lead_payload,
        "children": children,
    }


@router.get("/{set_id}")
def get_fanout(set_id: str,
               user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    fanout = db.execute(
        scoped(FanoutSet, user.org_id).where(FanoutSet.id == set_id)
    ).scalar_one_or_none()
    if fanout is None:
        raise HTTPException(404, "Fan-out set not found.")

    # Project children with per-run trust state. Reads the SAME
    # compact_trust_state every other surface uses — single source.
    run_ids = [c["run_id"] for c in (fanout.children or [])]
    runs_by_id: dict[str, Run] = {}
    arts_by_run: dict[str, Artifact] = {}
    if run_ids:
        runs = db.execute(
            scoped(Run, user.org_id).where(Run.id.in_(run_ids))
        ).scalars().all()
        runs_by_id = {r.id: r for r in runs}
        arts = db.execute(
            scoped(Artifact, user.org_id).where(
                Artifact.run_id.in_(run_ids),
                Artifact.type.in_(_DERIVATIVE_TYPE_SET),
            )
        ).scalars().all()
        arts_by_run = {a.run_id: a for a in arts}

    projected_children: list[dict] = []
    states_seen: set[str] = set()
    for c in (fanout.children or []):
        run = runs_by_id.get(c["run_id"])
        art = arts_by_run.get(c["run_id"])
        trust_state = None
        if art:
            tc = (art.body or {}).get("trust_checks") or {}
            trust_state = compact_trust_state(tc)
            if trust_state:
                states_seen.add(trust_state)
        projected_children.append({
            "derivative_type": c["derivative_type"],
            "run_id": c["run_id"],
            "run_status": run.status if run else None,
            "run_error": run.error if run else None,
            "artifact_id": art.id if art else None,
            "artifact_title": art.title if art else None,
            "trust_state": trust_state,
            "approval_blocked": bool(
                ((art.body or {}).get("trust_checks") or {})
                .get("approval_blocked")) if art else None,
        })

    # Aggregate set status from per-run status, not per-child trust.
    # A blocked sibling has run.status='succeeded' AND trust_state=
    # 'blocked' — distinct from a failed run (which has its own
    # error). "complete" = every child reached a terminal state.
    terminal = {"succeeded", "failed"}
    if not run_ids:
        set_status = "empty"
    elif all((c["run_status"] in terminal) for c in projected_children):
        set_status = "complete"
    else:
        set_status = "in_progress"

    return {
        "id": fanout.id,
        "source_anchor_id": fanout.source_anchor_id,
        "source_anchor_type": fanout.source_anchor_type,
        "source_anchor_title": fanout.source_anchor_title,
        "lead_ev_id": fanout.lead_ev_id,
        "lead_payload": fanout.lead_payload,
        "set_status": set_status,
        "children": projected_children,
        # Honest per-child trust roll-up — the set has NO trust of
        # its own; this just summarizes what each sibling holds.
        "trust_states_present": sorted(states_seen),
        "created_at": fanout.created_at.isoformat() if fanout.created_at else None,
    }
