"""
Dashboard endpoints — upload reports, read the funnel view, manage
evidence-attached suggestions.

All reads/writes tenant-scoped via scoped(). The dashboard is a query
layer over `metric_points` (the generic time-series receptacle); the
ingest reuses the forgiving CSV approach used elsewhere.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import current_user
from app.dashboard.funnel import funnel_view
from app.dashboard.ingest import parse_report_csv
from app.dashboard.suggestions import (industry_suggestions,
                                       trend_suggestions)
from app.db import get_db
from app.models import MetricPoint, OrgProfile, ReportUpload, Suggestion, User
from app.tenancy import scoped

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


_VALID_MODES = {"baseline", "ongoing"}


def _serialize_upload(u: ReportUpload, *, point_count: int | None = None) -> dict:
    return {
        "id": u.id, "filename": u.filename, "source": u.source, "mode": u.mode,
        "column_mapping": u.column_mapping or {},
        "point_count": point_count if point_count is not None else u.point_count,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _serialize_suggestion(s: Suggestion) -> dict:
    return {
        "id": s.id, "kind": s.kind, "recommendation": s.recommendation,
        "evidence": s.evidence or {}, "confidence": s.confidence,
        "source_label": s.source_label, "status": s.status,
        "idea_content_type": s.idea_content_type,
        "idea_topic": s.idea_topic, "idea_target": s.idea_target,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _profile_dict(db: Session, org_id: str) -> dict | None:
    """Confirmed OrgProfile as a plain dict — same shape the agent uses.
    Returns None when there's no confirmed profile (suggestions can still
    fire deterministic trend rules; industry just degrades to fallback)."""
    prof = db.execute(scoped(OrgProfile, org_id)).scalar_one_or_none()
    if prof is None or not prof.confirmed:
        return None
    return {
        "product_summary": prof.product_summary,
        "value_prop": prof.value_prop,
        "icp": prof.icp or {},
        "competitors": prof.competitors or [],
        "keywords": prof.keywords or [],
        "brand_voice": prof.brand_voice,
        "banned_claims": prof.banned_claims or [],
        "conversion_goal": prof.conversion_goal,
    }


@router.post("/reports")
async def upload_report(file: UploadFile = File(...),
                        mode: str = Form("ongoing"),
                        source: str = Form("manual"),
                        user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
    """Ingest one report CSV.

    `mode` ∈ {baseline, ongoing} — baseline rows feed the trend BACKDROP
    only; ongoing rows are attributed to produced content where their UTM
    matches an artifact's tag (the join key the content engine stamps).

    Forgiving CSV: header mapping is case/space/underscore-insensitive,
    unrecognized columns are silently dropped (and reported in the
    response), a missing date column or zero parsed points returns an
    HTTP 200 with `point_count: 0` and the column mapping so the user can
    see why nothing landed — we never crash on a noisy export.
    """
    if mode not in _VALID_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(_VALID_MODES)}")
    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")

    parsed = parse_report_csv(text)
    upload = ReportUpload(
        org_id=user.org_id, filename=file.filename or "report.csv",
        source=(source or "manual").strip().lower(),
        mode=mode, column_mapping=parsed["column_mapping"],
        point_count=len(parsed["points"]), uploaded_by=user.id,
    )
    db.add(upload)
    db.flush()   # need upload.id before inserting points

    is_baseline = (mode == "baseline")
    for p in parsed["points"]:
        db.add(MetricPoint(
            org_id=user.org_id, report_upload_id=upload.id,
            source=upload.source,
            metric_name=p["metric_name"], value=p["value"], date=p["date"],
            segment=p.get("segment") or "",
            utm_campaign=p.get("utm_campaign") or None,
            utm_source=p.get("utm_source") or None,
            utm_medium=p.get("utm_medium") or None,
            utm_content=p.get("utm_content") or None,
            raw_ref=p.get("raw_ref") or {},
            is_baseline=is_baseline,
        ))
    db.commit()
    db.refresh(upload)
    return {
        **_serialize_upload(upload, point_count=len(parsed["points"])),
        "format": parsed["format"],
        "skipped_rows": parsed["skipped_rows"],
        # Surface the first few normalized points so the user can sanity-
        # check the column mapping without a separate request.
        "preview": [
            {"metric_name": p["metric_name"], "stage": p.get("stage"),
             "value": p["value"], "date": p["date"].isoformat(),
             "segment": p.get("segment") or "",
             "utm_campaign": p.get("utm_campaign"),
             "utm_source": p.get("utm_source")}
            for p in parsed["points"][:5]
        ],
    }


@router.get("/reports")
def list_reports(user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    rows = db.execute(
        scoped(ReportUpload, user.org_id).order_by(ReportUpload.created_at.desc())
    ).scalars().all()
    return [_serialize_upload(u) for u in rows]


@router.get("/funnel")
def get_funnel(bucket: str = "week", user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    if bucket not in ("day", "week", "month"):
        raise HTTPException(400, "bucket must be one of day | week | month")
    return funnel_view(db, user.org_id, bucket=bucket)


@router.post("/suggestions/refresh")
def refresh_suggestions(user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """Recompute suggestions from the current funnel view + profile.

    Two source kinds get persisted: deterministic trend rules (always
    available) and LLM industry perspective (falls back to a single
    labeled "unavailable" item without a key). We DELETE prior `open`
    suggestions before inserting fresh ones so the cockpit reflects
    today's data — `dismissed` / `actioned` history is preserved.
    """
    db.execute(
        scoped(Suggestion, user.org_id)
        .where(Suggestion.status == "open")
        .with_only_columns(Suggestion.id)
    )
    # SQLAlchemy 2: do the delete in two passes — fetch ids, then delete.
    open_rows = db.execute(
        scoped(Suggestion, user.org_id).where(Suggestion.status == "open")
    ).scalars().all()
    for s in open_rows:
        db.delete(s)
    db.flush()

    profile = _profile_dict(db, user.org_id)
    funnel = funnel_view(db, user.org_id)
    fresh = list(trend_suggestions(funnel, profile)) + list(
        industry_suggestions(profile, funnel))

    persisted: list[Suggestion] = []
    for item in fresh:
        row = Suggestion(
            org_id=user.org_id,
            kind=item["kind"],
            recommendation=item["recommendation"],
            evidence=item.get("evidence") or {},
            confidence=item.get("confidence") or "medium",
            source_label=item.get("source_label") or "",
            idea_content_type=item.get("idea_content_type"),
            idea_topic=item.get("idea_topic"),
            idea_target=item.get("idea_target"),
            status="open",
        )
        db.add(row)
        persisted.append(row)
    db.commit()
    for s in persisted:
        db.refresh(s)
    return [_serialize_suggestion(s) for s in persisted]


@router.get("/suggestions")
def list_suggestions(status: str = "open",
                     user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    rows = db.execute(
        scoped(Suggestion, user.org_id)
        .where(Suggestion.status == status)
        .order_by(Suggestion.created_at.desc())
    ).scalars().all()
    return [_serialize_suggestion(s) for s in rows]


@router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(suggestion_id: str,
                       user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    s = db.execute(
        scoped(Suggestion, user.org_id).where(Suggestion.id == suggestion_id)
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "Suggestion not found")
    if s.status != "open":
        raise HTTPException(409, f"Suggestion already {s.status}")
    s.status = "dismissed"
    db.commit()
    return _serialize_suggestion(s)
