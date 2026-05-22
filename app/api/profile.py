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
from app.setup.draft import (
    draft_from_crawl, draft_from_csv_rows, draft_from_knowledge, name_from_domain,
)
from app.setup.merge import merge_profiles
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


class PreviewMergeIn(BaseModel):
    """Compute a per-field merge diff WITHOUT writing anything.
      * against="saved"   → diff the incoming draft against the org's saved
                            profile (enrichment flow 2). base is ignored.
      * against="working" → diff against the client's current working draft
                            (first-time layering flow 1); base carries it.
    """
    incoming: dict
    against: str = "saved"
    base: dict | None = None


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


@router.post("/preview-merge")
def preview_merge(body: PreviewMergeIn,
                  user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Read-only: compute the per-field diff of layering `incoming` onto the
    base (saved profile or the client's working draft). Writes NOTHING — PUT
    remains the only writer of confirmed truth. The client renders the
    changelist for accept/reject and PUTs the result of the accepted subset."""
    if body.against == "saved":
        prof = _get_profile(db, user.org_id)
        base = _serialize(prof) if prof else _empty_payload(user.org_id)
        base_exists = prof is not None and bool(prof.confirmed)
    else:  # "working" — layering during first-time setup
        base = body.base or _empty_payload(user.org_id)
        base_exists = False
    result = merge_profiles(base, body.incoming or {})
    return {
        "merged": result["merged"],
        "changes": result["changes"],
        "against": body.against,
        "base_exists": base_exists,
    }


@router.post("/crawl")
def crawl_profile(body: CrawlIn, user: User = Depends(current_user)):
    """Crawl a website and draft a profile. Never saves. Crawl failures DO NOT
    raise — they come back as a draft with status info in crawl_summary, so
    the UI can fall back to manual mode without a 500."""
    if not (body.url or "").strip():
        raise HTTPException(400, "url is required")
    result = crawl_site(body.url)
    if result.get("status") == "ok":
        # Happy path: real HTML available, LLM proposes from crawl text.
        draft = draft_from_crawl(result)
        draft_source = "crawl"
    else:
        # Crawl failed (Cloudflare 403, DNS miss, JS-only, timeout...). Fall
        # back to "what we know about this company" via the LLM — same draft
        # shape, source tagged "knowledge". With no API key (or on any LLM
        # error), this returns the minimal skeleton, so the manual form path
        # remains the always-works fallback.
        from urllib.parse import urlparse
        parsed = urlparse(result.get("url") or "")
        domain = (parsed.netloc or "").lower() or (body.url or "").strip()
        draft = draft_from_knowledge(domain, name_from_domain(domain))
        # "knowledge" if the LLM actually contributed something; "skeleton"
        # if every field is blank (model didn't recognize the company OR
        # there's no API key). The UI uses this to pick the right banner.
        draft_source = "knowledge" if (draft.get("source") or {}) else "skeleton"
    # Lift any LLM failure reason out of the draft and onto the response, so
    # the UI can say "AI drafting unavailable: credit balance too low" instead
    # of silently showing a blank skeleton. Keeps the draft itself clean
    # (profile-shaped). None when the LLM succeeded or wasn't relevant.
    llm_error = draft.pop("llm_error", None)
    return {
        "draft": draft,
        "crawl_status": result.get("status"),
        "crawl_message": result.get("message"),
        # Structured error for the UI to render a specific, actionable reason
        # ("HTTP 403 — Cloudflare", "DNS lookup failed", ...). None on success.
        "crawl_error": result.get("error"),
        # Which drafter produced this: "crawl" | "knowledge" | "skeleton".
        # Drives the banner copy in ui.html (honest labeling — see CLAUDE.md).
        "draft_source": draft_source,
        # {type, message, friendly} when the LLM call failed or was skipped
        # (no key); None otherwise. friendly is a UI-ready one-liner.
        "llm_error": llm_error,
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
