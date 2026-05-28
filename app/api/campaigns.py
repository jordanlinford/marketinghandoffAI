"""
Campaigns API — the orchestration layer surface.

THE LOAD-BEARING PRINCIPLE: campaigns REFERENCE artifacts; they do NOT
own content-generation logic. /generate enqueues a content_engine Run
PER plan item — the existing agent + worker path does the work. Generated
artifacts carry `campaign_id` as a back-reference and remain first-class
library citizens. Archive ≠ delete content.

Endpoints (all tenant-scoped via scoped()):
  POST   /api/campaigns                  — create draft (Step 1).
  GET    /api/campaigns                  — list with optional filters.
  GET    /api/campaigns/{id}             — detail (auto-refreshes
                                           generated_asset_ids from
                                           current artifacts).
  PATCH  /api/campaigns/{id}             — edit metadata / targeting /
                                           channels / plan (Step 3).
  POST   /api/campaigns/{id}/propose     — ONE LLM call → plan (Step 2).
  POST   /api/campaigns/{id}/generate    — Step 4: enqueue N content_engine
                                           runs with shared utm_campaign.
  DELETE /api/campaigns/{id}             — archive (soft); assets stay.
"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.utm import build_tagged_url
from app.auth import current_user
from app.campaigns.planner import propose_plan, recommend_channels
from app.db import get_db
from app.models import (AgentRegistration, Artifact, Campaign, ProductProfile,
                        Run, User, _now)
from app.products import resolve_product_profile
from app.queue import enqueue
from app.tenancy import scoped

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


_VALID_TYPES = {"awareness", "demand_gen", "launch", "nurture",
                "competitive", "other"}
_VALID_STATUS = {"draft", "planned", "generating", "active", "complete",
                 "archived"}


def _slug(text: str, max_len: int = 80) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:max_len] or "campaign"


def _serialize(c: Campaign) -> dict:
    return {
        "id": c.id, "org_id": c.org_id, "product_id": c.product_id,
        "name": c.name, "description": c.description,
        "campaign_type": c.campaign_type, "objective": c.objective,
        "status": c.status, "owner": c.owner,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "parent_asset_id": c.parent_asset_id,
        "primary_cta": c.primary_cta,
        "target_personas": c.target_personas or [],
        "target_segments": c.target_segments or [],
        "target_industries": c.target_industries or [],
        "target_account_ref": c.target_account_ref,
        "selected_channels": c.selected_channels or [],
        "channel_recommendations": c.channel_recommendations or {},
        "channel_notes": c.channel_notes,
        "plan": c.plan or {},
        "generated_asset_ids": c.generated_asset_ids or [],
        "utm_campaign": c.utm_campaign,
        "kpis": c.kpis, "linked_metric_point_query": c.linked_metric_point_query,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _get(db: Session, user: User, campaign_id: str) -> Campaign:
    c = db.execute(
        scoped(Campaign, user.org_id).where(Campaign.id == campaign_id)
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


def _refresh_generated_asset_ids(db: Session, c: Campaign) -> None:
    """Pull the current list from the truth (scoped artifacts with the
    matching campaign_id). Keeps the convenience column in sync without
    relying on the worker to write back to a campaign-domain table."""
    rows = db.execute(
        scoped(Artifact, c.org_id).where(Artifact.campaign_id == c.id)
        .order_by(Artifact.created_at.asc())
    ).scalars().all()
    c.generated_asset_ids = [a.id for a in rows]


def _maybe_flip_generating_to_active(c: Campaign) -> None:
    # When every approved plan item has produced an artifact, the batch
    # is done — flip to active. Only acts on status == 'generating' so we
    # never overwrite draft / planned / archived. Counts side: pulling
    # from c.generated_asset_ids assumes _refresh ran first.
    if c.status != "generating":
        return
    expected = len(((c.plan or {}).get("derivative_assets")) or [])
    if expected and len(c.generated_asset_ids or []) >= expected:
        c.status = "active"


# ---- Create ---------------------------------------------------------------
class CampaignCreateIn(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    campaign_type: str = "other"
    objective: str = ""
    primary_cta: str = ""
    product_id: str | None = None
    parent_asset_id: str | None = None
    target_personas: list = Field(default_factory=list)
    target_segments: list = Field(default_factory=list)
    target_industries: list = Field(default_factory=list)
    selected_channels: list = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None


@router.post("", status_code=201)
def create_campaign(body: CampaignCreateIn,
                    user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    ctype = (body.campaign_type or "other").lower()
    if ctype not in _VALID_TYPES:
        raise HTTPException(400, f"campaign_type must be in {sorted(_VALID_TYPES)}")
    # Validate optional product + parent asset against the caller's org.
    product_id: str | None = None
    if body.product_id:
        p = db.execute(
            scoped(ProductProfile, user.org_id)
            .where(ProductProfile.id == body.product_id)
        ).scalar_one_or_none()
        if p is None:
            raise HTTPException(404, "Product not found for this org")
        product_id = p.id
    parent_asset_id: str | None = None
    if body.parent_asset_id:
        art = db.execute(
            scoped(Artifact, user.org_id)
            .where(Artifact.id == body.parent_asset_id)
        ).scalar_one_or_none()
        if art is None:
            raise HTTPException(404, "Parent asset not found for this org")
        parent_asset_id = art.id

    # Compute the coordinated utm_campaign slug. Mirrors Build A's
    # product-prefix convention so attribution reports segment naturally.
    base_slug = _slug(body.name)
    if product_id:
        prod = db.get(ProductProfile, product_id)
        if prod and prod.slug:
            base_slug = f"{_slug(prod.slug)}__{base_slug}"
    utm_campaign = f"campaign-{base_slug}"

    c = Campaign(
        org_id=user.org_id, product_id=product_id,
        name=body.name, description=body.description,
        campaign_type=ctype, objective=body.objective,
        status="draft", owner=user.email, primary_cta=body.primary_cta,
        parent_asset_id=parent_asset_id,
        target_personas=body.target_personas,
        target_segments=body.target_segments,
        target_industries=body.target_industries,
        selected_channels=body.selected_channels,
        utm_campaign=utm_campaign,
        start_date=date.fromisoformat(body.start_date) if body.start_date else None,
        end_date=date.fromisoformat(body.end_date) if body.end_date else None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize(c)


# ---- List ----------------------------------------------------------------
@router.get("")
def list_campaigns(product_id: str | None = Query(None),
                   status: str | None = Query(None),
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> list[dict]:
    q = scoped(Campaign, user.org_id).order_by(Campaign.created_at.desc())
    if product_id:
        q = q.where(Campaign.product_id == product_id)
    if status:
        q = q.where(Campaign.status == status)
    rows = db.execute(q).scalars().all()
    # Auto-flip generating → active on read (same logic as detail GET) so the
    # list view doesn't lag behind reality. List page is what the UI hits
    # while the user is waiting for a batch to finish.
    flipped = False
    for c in rows:
        if c.status == "generating":
            _refresh_generated_asset_ids(db, c)
            _maybe_flip_generating_to_active(c)
            if c.status == "active":
                flipped = True
    if flipped:
        db.commit()
    return [_serialize(c) for c in rows]


# ---- Detail (auto-refresh generated_asset_ids) ---------------------------
@router.get("/{campaign_id}")
def get_campaign(campaign_id: str,
                 user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    c = _get(db, user, campaign_id)
    _refresh_generated_asset_ids(db, c)
    _maybe_flip_generating_to_active(c)
    db.commit()
    db.refresh(c)
    return _serialize(c)


# ---- Patch (Step 3 + general edits) --------------------------------------
class CampaignPatchIn(BaseModel):
    name: str | None = None
    description: str | None = None
    campaign_type: str | None = None
    objective: str | None = None
    primary_cta: str | None = None
    parent_asset_id: str | None = None
    target_personas: list | None = None
    target_segments: list | None = None
    target_industries: list | None = None
    selected_channels: list | None = None
    channel_recommendations: dict | None = None
    channel_notes: dict | None = None
    plan: dict | None = None
    status: str | None = None
    start_date: str | None = None
    end_date: str | None = None


@router.patch("/{campaign_id}")
def patch_campaign(campaign_id: str, body: CampaignPatchIn,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    c = _get(db, user, campaign_id)
    explicit = body.model_dump(exclude_unset=True)
    if "campaign_type" in explicit:
        v = (explicit["campaign_type"] or "other").lower()
        if v not in _VALID_TYPES:
            raise HTTPException(400, f"campaign_type must be in {sorted(_VALID_TYPES)}")
        c.campaign_type = v
    if "status" in explicit:
        v = (explicit["status"] or c.status).lower()
        if v not in _VALID_STATUS:
            raise HTTPException(400, f"status must be in {sorted(_VALID_STATUS)}")
        c.status = v
    if "parent_asset_id" in explicit:
        new = explicit["parent_asset_id"]
        if new:
            art = db.execute(
                scoped(Artifact, user.org_id).where(Artifact.id == new)
            ).scalar_one_or_none()
            if art is None:
                raise HTTPException(404, "Parent asset not found for this org")
            c.parent_asset_id = art.id
        else:
            c.parent_asset_id = None
    for key in ("name", "description", "objective", "primary_cta",
                "target_personas", "target_segments", "target_industries",
                "selected_channels", "channel_recommendations",
                "channel_notes", "plan"):
        if key in explicit:
            setattr(c, key, explicit[key])
    for key in ("start_date", "end_date"):
        if key in explicit:
            v = explicit[key]
            setattr(c, key, date.fromisoformat(v) if v else None)
    # The brief's Step-3 contract: PATCHing the plan implicitly moves the
    # campaign forward to "planned" (the user has approved the plan)
    # unless they explicitly set status themselves.
    if "plan" in explicit and "status" not in explicit and c.status == "draft":
        c.status = "planned"
    c.updated_at = _now()
    db.commit()
    db.refresh(c)
    return _serialize(c)


# ---- Propose (Step 2) ----------------------------------------------------
@router.post("/{campaign_id}/propose")
def propose(campaign_id: str,
            user: User = Depends(current_user),
            db: Session = Depends(get_db)) -> dict:
    """Single cheap LLM call → structured plan. Persists the plan +
    channel recommendations and leaves status='draft' so the human can
    edit it (Step 3) before any generation."""
    c = _get(db, user, campaign_id)
    profile = resolve_product_profile(db, user.org_id, c.product_id)
    parent_summary = ""
    if c.parent_asset_id:
        parent = db.execute(
            scoped(Artifact, user.org_id).where(Artifact.id == c.parent_asset_id)
        ).scalar_one_or_none()
        if parent is not None:
            body = parent.body or {}
            blocks = (body.get("content") or {}).get("blocks") or []
            parent_summary = (parent.title or "") + "\n" + "\n".join(
                b.get("text", "") for b in blocks)[:1500]
    plan, cost = propose_plan(c, profile, parent_summary)
    c.plan = plan
    # Recommendations are saved separately so the UI can show them next
    # to the plan. Same data; different read path.
    c.channel_recommendations = recommend_channels(
        c.campaign_type, c.selected_channels, c.primary_cta)
    c.updated_at = _now()
    db.commit()
    db.refresh(c)
    return {**_serialize(c), "_propose_cost_usd": cost}


# ---- Generate (Step 4) ---------------------------------------------------
@router.post("/{campaign_id}/generate")
def generate(campaign_id: str,
             user: User = Depends(current_user),
             db: Session = Depends(get_db)) -> dict:
    """For each approved plan item, enqueue ONE content_engine Run with:
       * task.action = generate
       * task.content_type / topic / target from the plan item
       * task.utm — the shared utm_campaign and per-item source/medium/content
       * task.campaign_id — back-reference the worker will stamp on the
         persisted artifact.
    The agent + worker path is unchanged — Campaign Builder does not
    duplicate generation logic. Generated assets honor content_review_mode
    (gate_all → land in the approval queue as proposals; guardrail / all_
    through as today)."""
    c = _get(db, user, campaign_id)
    plan = c.plan or {}
    items = list((plan.get("derivative_assets") or []))
    if not items:
        raise HTTPException(409, "Campaign has no approved plan items to generate.")
    # Resolve the content_engine registration once; same path /api/runs uses.
    reg = db.execute(
        scoped(AgentRegistration, user.org_id)
        .where(AgentRegistration.key == "content_engine")
    ).scalar_one_or_none()
    if reg is None or not reg.enabled:
        raise HTTPException(404, "content_engine agent is not enabled for this org")
    queued: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content_type = (item.get("content_type") or "email").strip()
        topic = (item.get("topic") or item.get("angle") or c.name).strip()
        target = (item.get("audience") or "").strip() or (
            ", ".join(c.target_personas[:2]) if c.target_personas else "")
        # Per-item UTMs: shared campaign, per-item source/medium/content
        # so attribution reports can slice the campaign by channel + type.
        item_slug = (item.get("id") or _slug(f"{content_type}-{topic[:40]}"))
        utm = {
            "utm_campaign": c.utm_campaign or f"campaign-{_slug(c.name)}",
            "utm_source": (item.get("channel") or "organic").strip().lower(),
            "utm_medium": content_type,
            "utm_content": item_slug,
        }
        # Carrying campaign_id on the task is how the worker stamps the
        # back-reference on the persisted artifact + proposal — no Run
        # column added (the back-ref lives on the asset, not the run).
        task = {
            "action": "generate",
            "content_type": content_type,
            "topic": topic,
            "target": target,
            "utm": utm,
            "campaign_id": c.id,
            "campaign_plan_item_id": item_slug,
        }
        run = Run(
            org_id=user.org_id, agent_registration_id=reg.id,
            agent_key="content_engine", trigger="campaign",
            status="queued", created_by=user.id,
            product_id=c.product_id, task=task,
        )
        db.add(run)
        db.flush()
        enqueue(db, user.org_id, "run_agent", {"run_id": run.id})
        queued.append({"run_id": run.id, "plan_item_id": item_slug,
                       "content_type": content_type})
    c.status = "generating"
    c.updated_at = _now()
    db.commit()
    db.refresh(c)
    return {"campaign": _serialize(c), "queued": queued,
            "queued_count": len(queued)}


# ---- Archive (soft delete) ------------------------------------------------
@router.delete("/{campaign_id}", status_code=200)
def archive_campaign(campaign_id: str,
                     user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    """Soft delete = status → archived. Generated artifacts stay in the
    library; ON DELETE SET NULL on the artifact.campaign_id back-ref
    means even a HARD delete would leave the assets independent. The
    point of the library is organizational memory; archiving a campaign
    should never erase work."""
    c = _get(db, user, campaign_id)
    c.status = "archived"
    c.updated_at = _now()
    db.commit()
    return {"id": c.id, "status": c.status,
            "note": "Archived. Generated assets remain in the Library."}


# ---- Library "Add to campaign" — attach as parent asset ------------------
class AttachAssetIn(BaseModel):
    """If `campaign_id` is set, attach the asset as the parent_asset of
    that existing campaign. If absent, the response is the routing
    payload the UI uses to land on a fresh-campaign form pre-filled
    with the parent asset."""
    campaign_id: str | None = None


@router.post("/-/attach-asset/{asset_kind}/{asset_id}")
def attach_asset(asset_kind: str, asset_id: str, body: AttachAssetIn,
                 user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    if asset_kind not in ("content", "brief"):
        # Only artifacts (content or brief) can be a campaign's parent
        # asset today — documents are intelligence, not a destination.
        raise HTTPException(400, "Only content or brief assets can anchor a campaign.")
    art = db.execute(
        scoped(Artifact, user.org_id).where(Artifact.id == asset_id)
    ).scalar_one_or_none()
    if art is None:
        raise HTTPException(404, "Asset not found for this org")
    if body.campaign_id:
        c = db.execute(
            scoped(Campaign, user.org_id).where(Campaign.id == body.campaign_id)
        ).scalar_one_or_none()
        if c is None:
            raise HTTPException(404, "Campaign not found for this org")
        c.parent_asset_id = art.id
        c.updated_at = _now()
        db.commit()
        db.refresh(c)
        return {"attached": True, "campaign": _serialize(c)}
    # No campaign_id → return the routing payload the UI uses to land
    # on a new-campaign form with the parent_asset_id pre-filled.
    return {
        "attached": False,
        "target_view": "campaigns",
        "parent_asset_id": art.id,
        "parent_asset_title": art.title or art.id,
        "product_id": art.product_id,
    }
