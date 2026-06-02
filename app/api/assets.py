"""
Asset Library — the organizational memory layer.

Unified, filterable, searchable view over three EXISTING sources:
  * content   → artifacts where type in ('content_draft', 'content_ideas')
  * brief     → artifacts where type == 'market_brief'
  * document  → product_documents

This module is primarily a READ projection — no new persistent state. The
single write is /duplicate, which calls the existing content-engine
artifact creation path. Document downloads re-serve the original file
through a tenant-scoped endpoint that checks org ownership; raw
filesystem paths are never exposed.

"Add to campaign" routes to the step-4 Campaign Builder placeholder
carrying the asset id in the response — no campaign is created.

All reads/writes go through scoped() per CLAUDE.md.
"""
from __future__ import annotations

import io
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import (Artifact, ProductDocument, ProductProfile, Run, User,
                        _now, _uuid)
from app.queue import enqueue
from app.tenancy import scoped

router = APIRouter(prefix="/api/assets", tags=["assets"])


# ---- Constants -------------------------------------------------------------
# Artifact types that count as content assets (drafts + idea lists).
_CONTENT_TYPES = ("content_draft", "content_ideas")
_BRIEF_TYPE = "market_brief"
# Reports are a top-level kind alongside content/document/brief. The
# stored Artifact.type for a report is "report_draft" — the body still
# carries body.content.content_type (report_board / report_ceo_weekly /
# report_sales_leadership) for audience, but the kind filter resolves
# off the top-level type. Same field every other kind uses; no new
# parallel field added.
# ---- Anchor-kind registry --------------------------------------------------
# Every Level-3 anchor follows the SAME mechanism: a top-level
# Artifact.type ("<anchor>_draft") drives the Library kind filter, the
# body.content.content_type carries the audience slug for badging, and
# the same intelligence engine + ledger + validator + severity layer +
# trust view-model power every anchor. New anchors register ONE entry
# here and the rest of the projection / list / detail paths pick them
# up automatically.
#
# Each entry:
#   artifact_type → {
#       "kind":             top-level asset_kind name (Library filter)
#       "content_types":    valid body.content.content_type values
#                           (badge text + "unknown" §5 guard)
#       "default_title":    fallback when art.title is empty
#       "title_label":      human noun for empty-state copy / search
#   }
_ANCHOR_KINDS: dict[str, dict] = {
    "report_draft": {
        "kind":           "report",
        "content_types":  ("report_board", "report_ceo_weekly",
                            "report_sales_leadership"),
        "default_title":  "Untitled report",
        "title_label":    "report",
    },
    "whitepaper_draft": {
        "kind":           "whitepaper",
        "content_types":  ("whitepaper",),
        "default_title":  "Untitled whitepaper",
        "title_label":    "whitepaper",
    },
    "buyer_guide_draft": {
        "kind":           "buyer_guide",
        "content_types":  ("buyer_guide",),
        "default_title":  "Untitled buyer's guide",
        "title_label":    "buyer's guide",
    },
    "solution_guide_draft": {
        "kind":           "solution_guide",
        "content_types":  ("solution_guide",),
        "default_title":  "Untitled solution guide",
        "title_label":    "solution guide",
    },
}

# Derived lookups so callers don't iterate the dict on every request.
_ANCHOR_TYPE_SET: tuple[str, ...] = tuple(_ANCHOR_KINDS.keys())
_ANCHOR_KIND_SET: tuple[str, ...] = tuple(
    spec["kind"] for spec in _ANCHOR_KINDS.values())
_ANCHOR_KIND_TO_TYPE: dict[str, str] = {
    spec["kind"]: art_type for art_type, spec in _ANCHOR_KINDS.items()
}

# ---- Derivative-kind registry --------------------------------------------
# Level-4 derivatives are SHORTER, channel-shaped re-expressions of
# Level-3 anchors. They share the projection + detail + Library
# plumbing with anchors (same trust pill, same trust_view, same body
# shape) — same kind-promotion mechanism, separate registry.
#
# A derivative's body carries body.source_anchor_id (the canonical
# lineage field); the projection surfaces it so the UI can render
# "derived from <anchor>" without a graph.
_DERIVATIVE_KINDS: dict[str, dict] = {
    "exec_summary_draft": {
        "kind":           "exec_summary",
        "content_types":  ("exec_summary",),
        "default_title":  "Untitled executive summary",
        "title_label":    "executive summary",
    },
    "carousel_draft": {
        "kind":           "carousel",
        "content_types":  ("carousel",),
        "default_title":  "Untitled carousel",
        "title_label":    "carousel",
    },
}

_DERIVATIVE_TYPE_SET: tuple[str, ...] = tuple(_DERIVATIVE_KINDS.keys())
_DERIVATIVE_KIND_SET: tuple[str, ...] = tuple(
    spec["kind"] for spec in _DERIVATIVE_KINDS.values())

# Back-compat constants. Some external surfaces still reference the
# specific "report" anchor (e.g. /api/reports). Anchors generally read
# off the registry above.
_REPORT_TYPE = "report_draft"
_REPORT_AUDIENCES = _ANCHOR_KINDS["report_draft"]["content_types"]
_KINDS: tuple[str, ...] = (
    ("content", "document", "brief")
    + _ANCHOR_KIND_SET + _DERIVATIVE_KIND_SET
)
_DEFAULT_LIMIT = 50


# ---- Projection helpers ----------------------------------------------------
def _product_names_map(db: Session, org_id: str) -> dict[str, str]:
    rows = db.execute(scoped(ProductProfile, org_id)).scalars().all()
    return {p.id: p.name for p in rows}


def _run_cost_map(db: Session, org_id: str, run_ids: set[str]) -> dict[str, float]:
    if not run_ids:
        return {}
    rows = db.execute(
        scoped(Run, org_id).where(Run.id.in_(run_ids))
    ).scalars().all()
    return {r.id: float(r.cost_usd or 0.0) for r in rows}


def _content_text_blob(art: Artifact) -> str:
    """Concatenated searchable text from a content_draft / content_ideas
    artifact — title + every block.text (content_draft) OR every idea's
    topic/rationale (content_ideas)."""
    body = art.body or {}
    pieces: list[str] = [art.title or ""]
    if art.type == "content_draft":
        content = body.get("content") or {}
        for b in (content.get("blocks") or []):
            t = b.get("text") or ""
            if t:
                pieces.append(t)
    elif art.type == "content_ideas":
        for idea in (body.get("ideas") or []):
            for k in ("topic", "rationale", "content_type", "target"):
                v = (idea.get(k) if isinstance(idea, dict) else None) or ""
                if v:
                    pieces.append(str(v))
    return " ".join(pieces)


def _brief_text_blob(art: Artifact) -> str:
    body = art.body or {}
    return " ".join(filter(None, [art.title or "", body.get("narrative") or ""]))


def _doc_text_blob(d: ProductDocument) -> str:
    return " ".join(filter(None, [d.filename or "", d.extracted_text or ""]))


def _project_anchor(art: Artifact, products: dict,
                    cost_by_run: dict) -> dict:
    """Single projection for every Level-3 anchor artifact (report,
    whitepaper, buyer_guide, solution_guide, ...). Reads the
    artifact_type → kind spec from _ANCHOR_KINDS; falls back to
    asset_kind='unknown' (§5 discipline) when the body.content
    .content_type doesn't match the spec's content_types — a
    defensive bug catcher so a malformed body doesn't get silently
    bucketed as a generic anchor.

    trust_state is the compact view-model state ('passed' /
    'passed_with_warnings' / 'blocked') so the Library list can
    render a trust pill on each card without fetching the detail.
    Reads off body.trust_checks via the SAME derivation the detail
    view-model uses (compact_trust_state) — single source of truth
    across reports, whitepapers, and every future anchor.
    """
    from app.reports.trust_view import compact_trust_state
    spec = _ANCHOR_KINDS.get(art.type)
    body = art.body or {}
    content = body.get("content") or {}
    metadata = content.get("metadata") or {}
    ct = (content.get("content_type") or "").strip()
    if spec and ct in spec["content_types"]:
        asset_kind = spec["kind"]
        # asset_type carries the audience/content slug — what the UI
        # badges with (e.g. report_board, whitepaper).
        asset_type = ct
        default_title = spec["default_title"]
    else:
        # Defensive §5: an anchor artifact whose content_type isn't in
        # its spec surfaces as 'unknown' rather than silently bucketed.
        asset_kind = "unknown"
        asset_type = "unknown"
        default_title = "Untitled anchor"
    return {
        "id": art.id,
        "asset_kind": asset_kind,
        "asset_type": asset_type,
        "title": art.title or metadata.get("topic") or default_title,
        "product_id": art.product_id,
        "product_name": products.get(art.product_id) if art.product_id else None,
        "status": art.status,
        "campaign": art.utm_campaign,
        "created_at": art.created_at.isoformat() if art.created_at else None,
        "updated_at": art.created_at.isoformat() if art.created_at else None,
        "cost_usd": cost_by_run.get(art.run_id),
        "grade": (art.grade or {}).get("overall") if art.grade else None,
        "trust_state": compact_trust_state(body.get("trust_checks")),
        "source_ref": {"table": "artifacts", "id": art.id,
                       "run_id": art.run_id, "artifact_type": art.type},
    }


def _project_derivative(art: Artifact, products: dict,
                        cost_by_run: dict) -> dict:
    """Projection for Level-4 derivative artifacts. Mirrors
    _project_anchor field-for-field plus the lineage fields
    (source_anchor_id, source_anchor_title, source_anchor_type) that
    the UI uses to render 'derived from <anchor>'. Trust pill reads
    from the SAME compact_trust_state — derivatives go through the
    SAME trust view-model as anchors, just driven by the containment
    validator's output instead of validate_evidence_binding."""
    from app.reports.trust_view import compact_trust_state
    spec = _DERIVATIVE_KINDS.get(art.type)
    body = art.body or {}
    content = body.get("content") or {}
    metadata = content.get("metadata") or {}
    ct = (content.get("content_type") or "").strip()
    if spec and ct in spec["content_types"]:
        asset_kind = spec["kind"]
        asset_type = ct
        default_title = spec["default_title"]
    else:
        asset_kind = "unknown"
        asset_type = "unknown"
        default_title = "Untitled derivative"
    return {
        "id": art.id,
        "asset_kind": asset_kind,
        "asset_type": asset_type,
        "title": art.title or metadata.get("topic") or default_title,
        "product_id": art.product_id,
        "product_name": products.get(art.product_id) if art.product_id else None,
        "status": art.status,
        "campaign": art.utm_campaign,
        "created_at": art.created_at.isoformat() if art.created_at else None,
        "updated_at": art.created_at.isoformat() if art.created_at else None,
        "cost_usd": cost_by_run.get(art.run_id),
        "grade": (art.grade or {}).get("overall") if art.grade else None,
        "trust_state": compact_trust_state(body.get("trust_checks")),
        # Lineage — canonical from body, mirrored into the projection
        # so the Library card can show the link without fetching detail.
        "source_anchor_id":    body.get("source_anchor_id"),
        "source_anchor_title": body.get("source_anchor_title"),
        "source_anchor_type":  body.get("source_anchor_type"),
        "source_ref": {"table": "artifacts", "id": art.id,
                       "run_id": art.run_id, "artifact_type": art.type},
    }


def _project_content(art: Artifact, products: dict, cost_by_run: dict) -> dict:
    body = art.body or {}
    content = body.get("content") or {}
    metadata = content.get("metadata") or {}
    if art.type == "content_draft":
        asset_type = content.get("content_type") or "content"
    else:
        asset_type = "content_ideas"
    return {
        "id": art.id,
        "asset_kind": "content",
        "asset_type": asset_type,
        "title": art.title or metadata.get("topic") or "Untitled content",
        "product_id": art.product_id,
        "product_name": products.get(art.product_id) if art.product_id else None,
        "status": art.status,
        "campaign": art.utm_campaign,
        "created_at": art.created_at.isoformat() if art.created_at else None,
        "updated_at": art.created_at.isoformat() if art.created_at else None,
        "cost_usd": cost_by_run.get(art.run_id),
        "grade": (art.grade or {}).get("overall") if art.grade else None,
        "source_ref": {"table": "artifacts", "id": art.id,
                       "run_id": art.run_id, "artifact_type": art.type},
    }


def _project_brief(art: Artifact, products: dict, cost_by_run: dict) -> dict:
    return {
        "id": art.id,
        "asset_kind": "brief",
        "asset_type": "market_brief",
        "title": art.title or "Market brief",
        "product_id": art.product_id,
        "product_name": products.get(art.product_id) if art.product_id else None,
        # Briefs land succeeded; status here describes the artifact, which
        # already carries 'ready' as the chassis default. Surface that.
        "status": art.status,
        "campaign": None,
        "created_at": art.created_at.isoformat() if art.created_at else None,
        "updated_at": art.created_at.isoformat() if art.created_at else None,
        "cost_usd": cost_by_run.get(art.run_id),
        "grade": None,
        "source_ref": {"table": "artifacts", "id": art.id,
                       "run_id": art.run_id, "artifact_type": art.type},
    }


def _project_document(d: ProductDocument, products: dict) -> dict:
    return {
        "id": d.id,
        "asset_kind": "document",
        "asset_type": d.kind,
        "title": d.filename,
        "product_id": d.product_id,
        "product_name": products.get(d.product_id),
        "status": d.status,
        "campaign": None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "cost_usd": None,
        "grade": None,
        "source_ref": {"table": "product_documents", "id": d.id},
    }


# ---- List endpoint ---------------------------------------------------------
def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except (TypeError, ValueError):
        return None


def _sort_key(sort: str):
    """Sort key + reverse flag. None values for grade/cost sort to end."""
    if sort == "oldest":
        return lambda a: a.get("created_at") or "", False
    if sort == "grade":
        return lambda a: (a.get("grade") is None, -1 * (a.get("grade") or 0)), False
    if sort == "cost":
        return lambda a: (a.get("cost_usd") is None, -1 * (a.get("cost_usd") or 0.0)), False
    # default = newest
    return lambda a: a.get("created_at") or "", True


@router.get("")
def list_assets(
    product_id: str | None = Query(None),
    asset_kind: str | None = Query(None),
    asset_type: str | None = Query(None),
    asset_type_prefix: str | None = Query(
        None, description="prefix match on content_type (e.g. 'report_')"),
    status: str | None = Query(None),
    campaign: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    q: str | None = Query(None, description="search text"),
    sort: str = Query("newest"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The unified asset list. Filters AND together (every supplied
    filter narrows the result). Search is a case-insensitive substring
    match across title + body text (per kind). No new tables — this is
    a query-time projection over artifacts + product_documents."""
    if asset_kind and asset_kind not in _KINDS:
        raise HTTPException(400, f"asset_kind must be one of {list(_KINDS)}")
    if sort not in ("newest", "oldest", "grade", "cost"):
        raise HTTPException(400, "sort must be one of newest|oldest|grade|cost")

    dfrom = _parse_iso_date(date_from)
    dto = _parse_iso_date(date_to)
    products = _product_names_map(db, user.org_id)
    needle = (q or "").strip().lower() or None

    projected: list[dict] = []
    run_ids: set[str] = set()
    # ---- Fetch artifacts (content + brief + anchors + derivatives) ----
    _fetch_kinds = ("content", "brief") + _ANCHOR_KIND_SET + _DERIVATIVE_KIND_SET
    if asset_kind in (None,) + _fetch_kinds:
        art_q = scoped(Artifact, user.org_id)
        # Filter artifact types up front so we don't drag every artifact
        # type into Python only to throw most away. The kind filter
        # resolves off the TOP-LEVEL Artifact.type — that's what the
        # spec calls the "existing asset-kind field" the dropdown
        # binds to. body.content.content_type is the audience slug,
        # consulted only inside the projection for badge text.
        types_wanted: list[str] = []
        if asset_kind in (None, "content"):
            types_wanted.extend(_CONTENT_TYPES)
        if asset_kind in (None, "brief"):
            types_wanted.append(_BRIEF_TYPE)
        # Every anchor (report, whitepaper, buyer_guide, solution_guide,
        # ...) is registered in _ANCHOR_KINDS — single source.
        for anchor_type, spec in _ANCHOR_KINDS.items():
            if asset_kind in (None, spec["kind"]):
                types_wanted.append(anchor_type)
        # Every derivative (exec_summary, ...) is registered in
        # _DERIVATIVE_KINDS — sibling registry, same plumbing.
        for deriv_type, spec in _DERIVATIVE_KINDS.items():
            if asset_kind in (None, spec["kind"]):
                types_wanted.append(deriv_type)
        art_q = art_q.where(Artifact.type.in_(types_wanted))
        if product_id:
            art_q = art_q.where(Artifact.product_id == product_id)
        if campaign:
            art_q = art_q.where(Artifact.utm_campaign == campaign)
        if status:
            art_q = art_q.where(Artifact.status == status)
        artifacts = db.execute(art_q).scalars().all()
        for a in artifacts:
            if dfrom and (a.created_at is None or a.created_at.date() < dfrom):
                continue
            if dto and (a.created_at is None or a.created_at.date() > dto):
                continue
            if a.type in _CONTENT_TYPES:
                if asset_type and (a.body or {}).get("content", {}) \
                        .get("content_type", "") != asset_type \
                        and asset_type != a.type:
                    continue
                if asset_type_prefix:
                    ct = (a.body or {}).get("content", {}) \
                        .get("content_type", "") or ""
                    if not ct.startswith(asset_type_prefix):
                        continue
                run_ids.add(a.run_id)
                projected.append(("__content__", a))
            elif a.type == _BRIEF_TYPE:
                if asset_type and asset_type not in (_BRIEF_TYPE, "brief"):
                    continue
                run_ids.add(a.run_id)
                projected.append(("__brief__", a))
            elif a.type in _ANCHOR_TYPE_SET:
                # Every anchor — report, whitepaper, buyer_guide,
                # solution_guide — funnels through the same filter +
                # projection. asset_type, when supplied, narrows to a
                # specific content_type (e.g. report_board, whitepaper)
                # OR matches the artifact_type itself.
                ct = (a.body or {}).get("content", {}) \
                    .get("content_type", "") or ""
                if asset_type and asset_type not in (ct, a.type):
                    continue
                if asset_type_prefix and not ct.startswith(asset_type_prefix):
                    continue
                run_ids.add(a.run_id)
                projected.append(("__anchor__", a))
            elif a.type in _DERIVATIVE_TYPE_SET:
                # Derivatives — exec_summary today — share the
                # filter/projection plumbing but go through a
                # lineage-aware projection that surfaces source_anchor_id.
                ct = (a.body or {}).get("content", {}) \
                    .get("content_type", "") or ""
                if asset_type and asset_type not in (ct, a.type):
                    continue
                if asset_type_prefix and not ct.startswith(asset_type_prefix):
                    continue
                run_ids.add(a.run_id)
                projected.append(("__derivative__", a))

    # ---- Fetch documents ---------------------------------------------
    if asset_kind in (None, "document"):
        # Documents excluded when filtering by campaign (they don't have one).
        if not campaign:
            doc_q = scoped(ProductDocument, user.org_id)
            if product_id:
                doc_q = doc_q.where(ProductDocument.product_id == product_id)
            if asset_type:
                doc_q = doc_q.where(ProductDocument.kind == asset_type)
            if status:
                doc_q = doc_q.where(ProductDocument.status == status)
            docs = db.execute(doc_q).scalars().all()
            for d in docs:
                if dfrom and (d.created_at is None or d.created_at.date() < dfrom):
                    continue
                if dto and (d.created_at is None or d.created_at.date() > dto):
                    continue
                projected.append(("__document__", d))

    # ---- Project + search ---------------------------------------------
    cost_by_run = _run_cost_map(db, user.org_id, run_ids)
    rows: list[dict] = []
    for tag, obj in projected:
        if tag == "__content__":
            asset = _project_content(obj, products, cost_by_run)
            if needle:
                if needle not in _content_text_blob(obj).lower():
                    continue
        elif tag == "__brief__":
            asset = _project_brief(obj, products, cost_by_run)
            if needle and needle not in _brief_text_blob(obj).lower():
                continue
        elif tag == "__anchor__":
            asset = _project_anchor(obj, products, cost_by_run)
            # Every anchor shares the content_draft body shape — reuse
            # the content text blob for search.
            if needle and needle not in _content_text_blob(obj).lower():
                continue
            # Defensive: when asset_kind resolves to 'unknown' the user
            # asked for an anchor kind but the artifact's content_type
            # didn't match the registry. We keep it in the result set
            # (it's still an artifact of an anchor type) but flagged.
        elif tag == "__derivative__":
            asset = _project_derivative(obj, products, cost_by_run)
            if needle and needle not in _content_text_blob(obj).lower():
                continue
        else:
            asset = _project_document(obj, products)
            if needle and needle not in _doc_text_blob(obj).lower():
                continue
        rows.append(asset)

    keyer, reverse = _sort_key(sort)
    rows.sort(key=keyer, reverse=reverse)
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"assets": page, "total": total, "offset": offset, "limit": limit}


# ---- Detail endpoints (native shape per kind) -----------------------------
def _load_content(db: Session, org_id: str, artifact_id: str) -> Artifact:
    # Every anchor (report / whitepaper / buyer_guide / solution_guide /
    # ...) AND every Level-4 derivative (exec_summary / ...) shares
    # the content_draft body shape — same block-based structure, same
    # grade attached, same .md download path. The URL is
    # /api/assets/content/{id} for all of them so existing detail +
    # download paths just work. The Library projection surfaces each
    # as its own asset_kind via the top-level Artifact.type field, so
    # the badge + filter resolve correctly per kind.
    accepted_types = (
        _CONTENT_TYPES + _ANCHOR_TYPE_SET + _DERIVATIVE_TYPE_SET
    )
    art = db.execute(
        scoped(Artifact, org_id).where(
            Artifact.id == artifact_id,
            Artifact.type.in_(accepted_types))
    ).scalar_one_or_none()
    if art is None:
        raise HTTPException(404, "Content asset not found")
    return art


def _load_brief(db: Session, org_id: str, artifact_id: str) -> Artifact:
    art = db.execute(
        scoped(Artifact, org_id).where(
            Artifact.id == artifact_id, Artifact.type == _BRIEF_TYPE)
    ).scalar_one_or_none()
    if art is None:
        raise HTTPException(404, "Brief asset not found")
    return art


def _load_document(db: Session, org_id: str, doc_id: str) -> ProductDocument:
    d = db.execute(
        scoped(ProductDocument, org_id).where(ProductDocument.id == doc_id)
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Document asset not found")
    return d


@router.get("/content/{artifact_id}")
def detail_content(artifact_id: str,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    art = _load_content(db, user.org_id, artifact_id)
    # asset_kind here mirrors the list projection: anchor + derivative
    # artifacts each surface as their own kind so the detail view's
    # badge + UI surfaces stay consistent with how the Library lists
    # them.
    anchor_spec = _ANCHOR_KINDS.get(art.type)
    deriv_spec = _DERIVATIVE_KINDS.get(art.type)
    if anchor_spec:
        asset_kind_out = anchor_spec["kind"]
    elif deriv_spec:
        asset_kind_out = deriv_spec["kind"]
    else:
        asset_kind_out = "content"
    response = {
        "asset_kind": asset_kind_out,
        "id": art.id, "title": art.title, "type": art.type,
        "status": art.status, "product_id": art.product_id,
        "run_id": art.run_id, "parent_id": art.parent_id,
        "body": art.body or {}, "citations": art.citations or [],
        "grade": art.grade,
        "utm_campaign": art.utm_campaign, "utm_source": art.utm_source,
        "utm_medium": art.utm_medium, "utm_content": art.utm_content,
        "destination_url": art.destination_url,
        "tagged_url": (art.body or {}).get("tagged_url"),
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }
    # Trust visibility surface (read-only rollup of EXISTING stored data
    # — no new validation, no new decisions). The view-model is
    # deterministic over body.trust_checks + body.evidence_ledger +
    # body.content.blocks. We compute on read so legacy artifacts work
    # without a backfill. Every anchor + every derivative shares the
    # trust surface — anchors go through validate_evidence_binding,
    # derivatives go through validate_containment, but both produce
    # the same trust_checks shape, so the view-model is identical.
    if art.type in _ANCHOR_TYPE_SET or art.type in _DERIVATIVE_TYPE_SET:
        from app.reports.trust_view import build_trust_view
        body = art.body or {}
        trust_checks = body.get("trust_checks") or {}
        evidence_ledger = body.get("evidence_ledger") or []
        blocks = ((body.get("content") or {}).get("blocks") or [])
        response["trust_view"] = build_trust_view(
            trust_checks, evidence_ledger, blocks)
    return response


@router.get("/brief/{artifact_id}")
def detail_brief(artifact_id: str,
                 user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    art = _load_brief(db, user.org_id, artifact_id)
    return {
        "asset_kind": "brief",
        "id": art.id, "title": art.title, "status": art.status,
        "product_id": art.product_id, "run_id": art.run_id,
        "body": art.body or {}, "citations": art.citations or [],
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }


@router.get("/document/{doc_id}")
def detail_document(doc_id: str,
                    user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    d = _load_document(db, user.org_id, doc_id)
    # Insights only surface here so the UI can show accepted candidates
    # next to the doc — same data the Products view already shows.
    from app.models import ExtractedInsight
    insights = db.execute(
        scoped(ExtractedInsight, user.org_id).where(
            ExtractedInsight.product_document_id == d.id)
    ).scalars().all()
    return {
        "asset_kind": "document",
        "id": d.id, "filename": d.filename, "mime_type": d.mime_type,
        "size_bytes": d.size_bytes, "sha256": d.sha256,
        "kind": d.kind, "version_label": d.version_label, "status": d.status,
        "extraction_error": d.extraction_error,
        "extracted_text_preview": (d.extracted_text or "")[:5000],
        "product_id": d.product_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "insights": [
            {"id": i.id, "field_name": i.field_name, "status": i.status,
             "confidence": i.confidence, "value": i.value}
            for i in insights
        ],
    }


# ---- Download / serve original ---------------------------------------------
def _compose_content_markdown(art: Artifact) -> str:
    body = art.body or {}
    content = body.get("content") or {}
    blocks = content.get("blocks") or []
    md_parts: list[str] = [f"# {art.title or 'Content'}\n"]
    for b in blocks:
        kind = (b.get("kind") or "block").replace("_", " ").title()
        text = (b.get("text") or "").rstrip()
        md_parts.append(f"## {kind}\n\n{text}\n")
    # Honest provenance footer — same discipline as the brief.
    grade = (art.grade or {}).get("overall") if art.grade else None
    if grade is not None:
        md_parts.append(f"\n---\n\n*Grade {grade} / 100 (advisory).*\n")
    utm_bits = " · ".join(filter(None, [
        f"campaign={art.utm_campaign}" if art.utm_campaign else None,
        f"source={art.utm_source}" if art.utm_source else None,
        f"medium={art.utm_medium}" if art.utm_medium else None,
        f"content={art.utm_content}" if art.utm_content else None,
    ]))
    if utm_bits:
        md_parts.append(f"\n*UTM: {utm_bits}*\n")
    tagged = (body.get("tagged_url") or "").strip()
    if tagged:
        md_parts.append(f"\n*Tagged URL: {tagged}*\n")
    return "\n".join(md_parts).strip() + "\n"


def _compose_brief_markdown(art: Artifact) -> str:
    body = art.body or {}
    out: list[str] = [f"# {art.title or 'Market brief'}\n"]
    narrative = (body.get("narrative") or "").strip()
    if narrative:
        out.append(narrative + "\n")
    # Structured details if present.
    structured = body.get("structured") or {}
    if isinstance(structured, dict):
        for section in ("recommendations", "top_targets",
                        "keyword_clusters", "inputs"):
            data = structured.get(section)
            if not data:
                continue
            heading = section.replace("_", " ").title()
            out.append(f"\n## {heading}\n")
            out.append("```json\n" + json.dumps(data, indent=2) + "\n```\n")
    citations = art.citations or []
    if citations:
        out.append("\n## Citations\n")
        for c in citations:
            src = c.get("source") if isinstance(c, dict) else None
            snip = c.get("snippet") if isinstance(c, dict) else None
            if src:
                out.append(f"- **{src}**" + (f" — {snip}" if snip else ""))
    return "\n".join(out).rstrip() + "\n"


def _ascii_filename(name: str | None, fallback: str = "asset") -> str:
    """HTTP Content-Disposition `filename=` is latin-1-only. Strip the
    title down to safe ASCII so an em-dash or curly quote in a title
    can't blow up the response (artifacts persist with em-dashes from
    the content_engine titles)."""
    text = (name or fallback).encode("ascii", "ignore").decode("ascii")
    text = text.replace("/", "_").strip().strip(".")
    return text[:80] or fallback


@router.get("/content/{artifact_id}/download")
def download_content(artifact_id: str,
                     format: str = Query("md", pattern="^(md|json)$"),
                     user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> Response:
    art = _load_content(db, user.org_id, artifact_id)
    safe_name = _ascii_filename(art.title or art.id)
    if format == "json":
        payload = json.dumps({
            "id": art.id, "title": art.title, "type": art.type,
            "status": art.status, "body": art.body or {},
            "citations": art.citations or [], "grade": art.grade,
            "utm_campaign": art.utm_campaign, "utm_source": art.utm_source,
            "utm_medium": art.utm_medium, "utm_content": art.utm_content,
            "destination_url": art.destination_url,
        }, indent=2)
        return Response(
            content=payload, media_type="application/json",
            headers={"Content-Disposition":
                     f'attachment; filename="{safe_name}.json"'})
    return Response(
        content=_compose_content_markdown(art),
        media_type="text/markdown",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}.md"'})


@router.get("/brief/{artifact_id}/download")
def download_brief(artifact_id: str,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Response:
    art = _load_brief(db, user.org_id, artifact_id)
    safe_name = _ascii_filename(art.title or art.id)
    return Response(
        content=_compose_brief_markdown(art),
        media_type="text/markdown",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}.md"'})


@router.get("/document/{doc_id}/download")
def download_document(doc_id: str,
                      user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    """Re-serve the ORIGINAL uploaded file through a tenant-scoped endpoint.
    The raw filesystem path is never exposed: we only ever serve files
    pointed to by a ProductDocument row that scoped() returned for this
    user's org. Cross-org access raises 404 above (scoped() returned None)."""
    d = _load_document(db, user.org_id, doc_id)
    path = (d.storage_path or "").strip()
    if not path or not Path(path).exists():
        raise HTTPException(410, "Original file is no longer available")
    return FileResponse(
        path, media_type=d.mime_type or "application/octet-stream",
        filename=_ascii_filename(d.filename, fallback="download.bin"))


@router.get("/document/{doc_id}/extracted-text")
def document_extracted_text(doc_id: str,
                            user: User = Depends(current_user),
                            db: Session = Depends(get_db)) -> Response:
    d = _load_document(db, user.org_id, doc_id)
    text = d.extracted_text or ""
    base = _ascii_filename(
        (d.filename or "document").rsplit(".", 1)[0], fallback="document")
    return Response(
        content=text, media_type="text/plain",
        headers={"Content-Disposition":
                 f'attachment; filename="{base}.extracted.txt"'})


# ---- Reuse ----------------------------------------------------------------
@router.post("/content/{artifact_id}/duplicate")
def duplicate_content(artifact_id: str,
                      user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    """Create an editable copy of a content_draft. The duplicate is a NEW
    artifact (with parent_id = source.id) on a synthetic Run, so the
    runs index keeps a coherent provenance trail. The content body +
    blocks are cloned verbatim — the user edits from there."""
    source = _load_content(db, user.org_id, artifact_id)
    if source.type != "content_draft":
        raise HTTPException(400, "Only content_draft assets can be duplicated.")
    # Synthetic Run so the artifact row's required run_id has a real
    # parent and the activity shows up in the Runs index. Cost = 0
    # because no agent ran.
    # We reuse the source's AgentRegistration id so the run links cleanly
    # — duplicates aren't agent invocations but they're indistinguishable
    # downstream and the chassis assumes runs always reference a reg.
    parent_run = db.execute(
        scoped(Run, user.org_id).where(Run.id == source.run_id)
    ).scalar_one_or_none()
    if parent_run is None:
        raise HTTPException(409, "Source run missing — cannot duplicate.")
    new_run = Run(
        org_id=user.org_id, agent_registration_id=parent_run.agent_registration_id,
        agent_key=parent_run.agent_key, trigger="duplicate",
        status="succeeded", created_by=user.id,
        product_id=source.product_id, task={"action": "duplicate",
                                            "source_artifact_id": source.id},
        cost_usd=0.0, started_at=_now(), finished_at=_now(),
    )
    db.add(new_run)
    db.flush()

    # Clone body verbatim — same blocks, same provenance fields, same
    # grade. The user edits the copy.
    body = dict(source.body or {})
    new_art = Artifact(
        org_id=user.org_id, run_id=new_run.id,
        product_id=source.product_id, parent_id=source.id,
        type="content_draft",
        title=f"Duplicate of {source.title or 'content'}",
        body=body, citations=source.citations or [],
        status="ready",
        grade=source.grade,
        utm_campaign=source.utm_campaign, utm_source=source.utm_source,
        utm_medium=source.utm_medium, utm_content=source.utm_content,
        destination_url=source.destination_url,
    )
    db.add(new_art)
    db.commit()
    db.refresh(new_art)
    return {"id": new_art.id, "run_id": new_run.id,
            "parent_id": source.id, "kind": "content"}


class RegenerateIn(BaseModel):
    """Optional caller overrides for the pre-fill. When absent, the
    backend extracts content_type + topic + target from the source."""
    content_type: str | None = None
    topic: str | None = None
    target: str | None = None


@router.post("/content/{artifact_id}/regenerate")
def regenerate_from_content(artifact_id: str, body: RegenerateIn,
                            user: User = Depends(current_user),
                            db: Session = Depends(get_db)) -> dict:
    """Pre-fill the content engine from a source content asset and queue
    a fresh generate run. The shape is the same as POST /api/runs with
    agent_key='content_engine' + task={action:'generate', ...} — but the
    caller doesn't have to extract the fields themselves."""
    source = _load_content(db, user.org_id, artifact_id)
    if source.type != "content_draft":
        raise HTTPException(400, "Regenerate-from only supports content_draft assets.")
    body_d = source.body or {}
    content = body_d.get("content") or {}
    metadata = content.get("metadata") or {}
    content_type = (body.content_type or content.get("content_type") or "email").strip()
    topic = (body.topic or metadata.get("topic")
             or (source.title or "Untitled")).strip()
    target = (body.target or metadata.get("target") or "").strip()
    return _enqueue_regen(db, user, source.product_id,
                          content_type, topic, target)


@router.post("/brief/{artifact_id}/regenerate")
def regenerate_from_brief(artifact_id: str, body: RegenerateIn,
                          user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict:
    """For briefs, "regenerate from this" means: start a content piece
    grounded in the brief's first recommendation (or its title)."""
    art = _load_brief(db, user.org_id, artifact_id)
    body_d = art.body or {}
    structured = body_d.get("structured") or {}
    recs = structured.get("recommendations") or []
    topic = (body.topic or (recs[0] if recs else (art.title or "Market brief"))).strip()
    content_type = (body.content_type or "email").strip()
    target = (body.target or "").strip()
    return _enqueue_regen(db, user, art.product_id,
                          content_type, topic, target)


def _enqueue_regen(db: Session, user: User, product_id: str | None,
                   content_type: str, topic: str, target: str) -> dict:
    """Common path for both content + brief regenerate. Mirrors the
    /api/runs logic so we don't reinvent run creation: validate the
    product belongs to the org, create the Run, enqueue the worker job."""
    if product_id:
        product = db.execute(
            scoped(ProductProfile, user.org_id)
            .where(ProductProfile.id == product_id)
        ).scalar_one_or_none()
        if product is None:
            # The source asset was scoped, but the product may have been
            # deleted since (ON DELETE SET NULL kept the asset). Drop
            # the product scope rather than fail the regen.
            product_id = None
    reg_q = scoped(__import__("app.models", fromlist=["AgentRegistration"])
                   .AgentRegistration, user.org_id).where(
        __import__("app.models", fromlist=["AgentRegistration"])
        .AgentRegistration.key == "content_engine")
    reg = db.execute(reg_q).scalar_one_or_none()
    if reg is None or not reg.enabled:
        raise HTTPException(404, "content_engine agent is not enabled for this org")
    run = Run(
        org_id=user.org_id, agent_registration_id=reg.id,
        agent_key="content_engine", trigger="manual",
        status="queued", created_by=user.id,
        product_id=product_id,
        task={"action": "generate", "content_type": content_type,
              "topic": topic, "target": target},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    enqueue(db, user.org_id, "run_agent", {"run_id": run.id})
    return {"id": run.id, "agent_key": run.agent_key, "status": run.status,
            "task": run.task, "product_id": run.product_id,
            "kind": "content"}


@router.post("/{asset_kind}/{asset_id}/route-to-campaign")
def route_to_campaign(asset_kind: str, asset_id: str,
                      user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    """PLACEHOLDER reuse path. Campaign Builder (step 4) is unbuilt —
    this endpoint validates the asset exists (and belongs to the org)
    and returns the routing payload the UI uses to land on the
    placeholder page with the asset id in state. NO campaign is created.
    When Campaign Builder ships, the wire is already here."""
    if asset_kind not in _KINDS:
        raise HTTPException(400, f"asset_kind must be one of {list(_KINDS)}")
    # Existence + ownership check — different table per kind.
    if asset_kind == "content":
        _load_content(db, user.org_id, asset_id)
    elif asset_kind == "brief":
        _load_brief(db, user.org_id, asset_id)
    else:
        _load_document(db, user.org_id, asset_id)
    return {
        "placeholder": True,
        "target_view": "campaigns",
        "asset_id": asset_id,
        "asset_kind": asset_kind,
        "note": ("Campaign Builder is not built yet. The UI will land on "
                 "the step-4 placeholder with this asset carried in state."),
    }
