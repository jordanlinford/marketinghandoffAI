"""
Setup-stage endpoints. The OrgProfile is the org-level config every later stage
reasons from (ICP, value prop, brand voice, conversion goal, ...). Three entry
paths, all tenant-scoped via `scoped()`:

  * GET  /api/profile             — read the org's profile (or empty skeleton).
  * PUT  /api/profile             — create/update; flips confirmed=true.
                                    This is the user's saved truth.
  * POST /api/profile/crawl       — fetch + LLM-draft a profile from a URL.
                                    Returns a DRAFT (confirmed=false), never saved.
  * POST /api/profile/from-csv    — infer a draft ICP from a customer-sample CSV.
                                    Returns a DRAFT (confirmed=false), never saved.

Only PUT writes confirmed truth. The crawl + from-csv paths surface proposals
the user reviews, edits, then re-submits via PUT — same honesty discipline as
the CSV intent column: the system PROPOSES, the user DISPOSES.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.uploads import _parse_csv as parse_uploaded_csv  # reuse, do not duplicate
from app.auth import current_user
from app.db import get_db
from app.models import OrgProfile, User, _now
from app.setup.crawl import crawl as crawl_site
from app.setup.draft import draft_from_crawl, draft_from_csv_rows
from app.tenancy import scoped

router = APIRouter(prefix="/api/profile", tags=["profile"])


_EMPTY_ICP = {
    "industries": [], "min_employees": None, "min_revenue_usd": None,
    "regions": [], "titles": [], "notes": "",
}


class ProfileIn(BaseModel):
    """Everything the user can edit. confirmed is set server-side on PUT."""
    product_summary: str = ""
    value_prop: str = ""
    icp: dict = Field(default_factory=lambda: dict(_EMPTY_ICP))
    competitors: list = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    brand_voice: str = ""
    banned_claims: list[str] = Field(default_factory=list)
    conversion_goal: str = ""
    conversion_event: str = ""
    website_url: str = ""
    crawl_summary: str = ""
    source: dict = Field(default_factory=dict)


class CrawlIn(BaseModel):
    url: str


def _empty_payload(org_id: str) -> dict:
    return {
        "id": None, "org_id": org_id, "confirmed": False, "exists": False,
        "product_summary": "", "value_prop": "",
        "icp": dict(_EMPTY_ICP),
        "competitors": [], "keywords": [],
        "brand_voice": "", "banned_claims": [],
        "conversion_goal": "", "conversion_event": "",
        "website_url": "", "crawl_summary": "",
        "source": {}, "updated_at": None, "created_at": None,
    }


def _serialize(p: OrgProfile) -> dict:
    return {
        "id": p.id, "org_id": p.org_id, "confirmed": bool(p.confirmed), "exists": True,
        "product_summary": p.product_summary, "value_prop": p.value_prop,
        "icp": p.icp or dict(_EMPTY_ICP),
        "competitors": p.competitors or [],
        "keywords": p.keywords or [],
        "brand_voice": p.brand_voice, "banned_claims": p.banned_claims or [],
        "conversion_goal": p.conversion_goal, "conversion_event": p.conversion_event,
        "website_url": p.website_url, "crawl_summary": p.crawl_summary,
        "source": p.source or {},
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _get_profile(db: Session, org_id: str) -> OrgProfile | None:
    return db.execute(scoped(OrgProfile, org_id)).scalar_one_or_none()


@router.get("")
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    prof = _get_profile(db, user.org_id)
    return _serialize(prof) if prof else _empty_payload(user.org_id)


@router.put("")
def put_profile(body: ProfileIn,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Upsert and confirm. One active profile per org (unique on org_id) — we
    update in place if one exists, insert otherwise."""
    prof = _get_profile(db, user.org_id)
    fields = body.model_dump()
    if prof is None:
        prof = OrgProfile(org_id=user.org_id, confirmed=True, **fields)
        db.add(prof)
    else:
        for k, v in fields.items():
            setattr(prof, k, v)
        prof.confirmed = True
        prof.updated_at = _now()
    db.commit()
    db.refresh(prof)
    return _serialize(prof)


@router.post("/crawl")
def crawl_profile(body: CrawlIn, user: User = Depends(current_user)):
    """Crawl a website and draft a profile. Never saves. Crawl failures DO NOT
    raise — they come back as a draft with status info in crawl_summary, so
    the UI can fall back to manual mode without a 500."""
    if not (body.url or "").strip():
        raise HTTPException(400, "url is required")
    result = crawl_site(body.url)
    draft = draft_from_crawl(result)
    return {
        "draft": draft,
        "crawl_status": result.get("status"),
        "crawl_message": result.get("message"),
        # Structured error for the UI to render a specific, actionable reason
        # ("HTTP 403 — Cloudflare", "DNS lookup failed", ...). None on success.
        "crawl_error": result.get("error"),
        "confirmed": False,  # explicit — the caller must PUT to save
    }


@router.post("/from-csv")
async def from_csv_profile(file: UploadFile = File(...),
                           user: User = Depends(current_user)):
    """Infer a draft ICP from an uploaded customer-sample CSV. Reuses the same
    header-mapping / range-revenue parser the /api/uploads endpoint uses, so
    'business_name', 'revenue_range', etc. all work the same way here."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")
    rows = parse_uploaded_csv(text)  # raises HTTPException(400) on bad headers
    if not rows:
        raise HTTPException(400, "CSV had a header but no data rows")
    draft = draft_from_csv_rows(rows)
    return {
        "draft": draft,
        "sample_rows": len(rows),
        "confirmed": False,
    }
