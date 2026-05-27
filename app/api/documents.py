"""
Product-document ingestion + candidate review API.

Two surfaces:
  * Documents:  POST /api/products/{id}/documents (upload),
                GET  /api/products/{id}/documents,
                POST /api/products/{id}/documents/{doc_id}/re-extract.
  * Insights:   GET   /api/products/{id}/insights,
                GET   /api/insights/{id},
                PATCH /api/insights/{id} (accept | edit | reject),
                POST  /api/products/{id}/insights/bulk_accept.

All reads/writes tenant-scoped via scoped(). Cross-org access fails closed.
Uploads land in {storage_root}/{org_id}/{product_id}/{doc_id}_{filename}
(see app/documents/storage.py). Extraction is async — the worker picks up
the queued job and runs normalize → extract → persist candidates.
"""
from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.documents.promote import promote_insight, restore_history_entry
from app.documents.storage import write_document
from app.models import (ExtractedInsight, ProductDocument, ProductProfile,
                        User, _now)
from app.queue import enqueue
from app.tenancy import scoped


# Two routers — the natural URL shapes are nested differently. Mounted
# separately in app/main.py.
products_doc_router = APIRouter(prefix="/api/products", tags=["documents"])
insights_router = APIRouter(prefix="/api/insights", tags=["insights"])

_ACCEPTED_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "text/plain", "text/markdown",
    "image/png", "image/jpeg", "image/jpg", "image/tiff",
}
_ACCEPTED_EXTS = {".pdf", ".docx", ".pptx", ".ppt", ".txt", ".md",
                  ".png", ".jpg", ".jpeg", ".tiff"}
_VALID_KINDS = {"messaging_framework", "one_pager", "launch_doc",
                "sales_enablement", "other"}


def _accepted(mime: str, filename: str) -> bool:
    mt = (mime or "").lower()
    if mt in _ACCEPTED_MIMES:
        return True
    lower = (filename or "").lower()
    return any(lower.endswith(ext) for ext in _ACCEPTED_EXTS)


def _get_product(db: Session, user: User, product_id: str) -> ProductProfile:
    p = db.execute(
        scoped(ProductProfile, user.org_id)
        .where(ProductProfile.id == product_id)
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Product not found")
    return p


def _serialize_doc(d: ProductDocument) -> dict:
    return {
        "id": d.id, "product_id": d.product_id, "filename": d.filename,
        "mime_type": d.mime_type, "size_bytes": d.size_bytes,
        "sha256": d.sha256, "kind": d.kind, "version_label": d.version_label,
        "status": d.status, "extraction_error": d.extraction_error,
        # Don't return the full extracted_text in the list view — heavy
        # and rarely needed. The re-extract endpoint reads it server-side.
        "has_extracted_text": bool(d.extracted_text),
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _serialize_insight(i: ExtractedInsight) -> dict:
    return {
        "id": i.id, "product_id": i.product_id,
        "product_document_id": i.product_document_id,
        "field_name": i.field_name, "value": i.value,
        "confidence": i.confidence, "source_passage": i.source_passage,
        "source_location": i.source_location, "dimensions": i.dimensions,
        "status": i.status, "accepted_value": i.accepted_value,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


# ---- Documents -----------------------------------------------------------
@products_doc_router.post("/{product_id}/documents", status_code=201)
async def upload_document(product_id: str,
                          file: UploadFile = File(...),
                          kind: str = Form("messaging_framework"),
                          version_label: str = Form(""),
                          user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict:
    """Persist a raw upload, create the document row, and enqueue an
    extraction job. Returns the document immediately so the UI can poll
    for status."""
    product = _get_product(db, user, product_id)
    if kind not in _VALID_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(_VALID_KINDS)}")
    if not _accepted(file.content_type or "", file.filename or ""):
        raise HTTPException(
            415,
            f"Unsupported file type {file.content_type!r} ({file.filename!r}). "
            f"Accepted: pdf, docx, pptx, txt, md, png, jpg, jpeg, tiff.")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty upload")
    sha = hashlib.sha256(raw).hexdigest()

    # Mark older docs of the same kind for this product as superseded —
    # the active extraction moves on, but rows + insights stay for audit.
    db.execute(
        scoped(ProductDocument, user.org_id)
        .where(ProductDocument.product_id == product.id,
               ProductDocument.kind == kind,
               ProductDocument.status.in_(("ingesting", "extracted")))
    ).scalars()
    for prior in db.execute(
        scoped(ProductDocument, user.org_id)
        .where(ProductDocument.product_id == product.id,
               ProductDocument.kind == kind,
               ProductDocument.status.in_(("ingesting", "extracted")))
    ).scalars():
        prior.status = "superseded"

    doc = ProductDocument(
        org_id=user.org_id, product_id=product.id,
        filename=file.filename or "upload",
        mime_type=(file.content_type or "").lower(),
        size_bytes=len(raw), sha256=sha,
        kind=kind, version_label=version_label.strip(),
        status="ingesting", storage_path="",
    )
    db.add(doc)
    db.flush()   # need doc.id for the storage path

    # Write the file to disk under the tenant path, then record it on the
    # row. If write_document raises, the transaction rolls back so we
    # don't leave a phantom row pointing at a missing file.
    try:
        path = write_document(user.org_id, product.id, doc.id, doc.filename, raw)
    except Exception:
        db.rollback()
        raise
    doc.storage_path = str(path)
    db.commit()
    db.refresh(doc)

    enqueue(db, user.org_id, "extract_document",
            {"product_document_id": doc.id})
    return _serialize_doc(doc)


@products_doc_router.get("/{product_id}/documents")
def list_documents(product_id: str,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> list[dict]:
    _get_product(db, user, product_id)
    rows = db.execute(
        scoped(ProductDocument, user.org_id)
        .where(ProductDocument.product_id == product_id)
        .order_by(ProductDocument.created_at.desc())
    ).scalars().all()
    return [_serialize_doc(d) for d in rows]


@products_doc_router.post("/{product_id}/documents/{doc_id}/re-extract")
def reextract_document(product_id: str, doc_id: str,
                       user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict:
    """Re-run extraction against the stored extracted_text without
    re-uploading the file. Useful when prompt/extractor logic changes."""
    _get_product(db, user, product_id)
    doc = db.execute(
        scoped(ProductDocument, user.org_id)
        .where(ProductDocument.id == doc_id,
               ProductDocument.product_id == product_id)
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Document not found")
    doc.status = "ingesting"
    doc.extraction_error = None
    doc.updated_at = _now()
    db.commit()
    enqueue(db, user.org_id, "extract_document",
            {"product_document_id": doc.id})
    return _serialize_doc(doc)


# ---- Insights (per-product list + per-id PATCH) --------------------------
@products_doc_router.get("/{product_id}/insights")
def list_insights(product_id: str, status: str | None = None,
                  user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> list[dict]:
    _get_product(db, user, product_id)
    q = (scoped(ExtractedInsight, user.org_id)
         .where(ExtractedInsight.product_id == product_id))
    if status:
        q = q.where(ExtractedInsight.status == status)
    q = q.order_by(ExtractedInsight.field_name.asc(),
                   ExtractedInsight.created_at.asc())
    return [_serialize_insight(i) for i in db.execute(q).scalars().all()]


@products_doc_router.post("/{product_id}/insights/bulk_accept")
def bulk_accept(product_id: str, body: dict,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    """Accept many candidates in one round trip. body = {ids: [...]}.
    Each id is validated via scoped() — IDs from other orgs / other
    products quietly drop out (we don't reveal whether they exist)."""
    product = _get_product(db, user, product_id)
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "body.ids must be a non-empty list")
    rows = db.execute(
        scoped(ExtractedInsight, user.org_id)
        .where(ExtractedInsight.id.in_(ids),
               ExtractedInsight.product_id == product.id,
               ExtractedInsight.status == "pending")
    ).scalars().all()
    accepted: list[dict] = []
    for insight in rows:
        try:
            result = promote_insight(product, insight)
        except ValueError as exc:
            insight.status = "rejected"
            insight.updated_at = _now()
            continue
        insight.status = "accepted"
        insight.accepted_value = insight.value
        insight.updated_at = _now()
        accepted.append({"id": insight.id, **result})
    product.updated_at = _now()
    db.commit()
    return {"accepted": accepted, "count": len(accepted)}


class InsightPatchIn(BaseModel):
    action: str            # 'accept' | 'edit' | 'reject'
    value: dict | list | str | None = None   # the edited value when action='edit'


@insights_router.get("/{insight_id}")
def get_insight(insight_id: str,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        scoped(ExtractedInsight, user.org_id)
        .where(ExtractedInsight.id == insight_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Insight not found")
    return _serialize_insight(row)


@insights_router.patch("/{insight_id}")
def patch_insight(insight_id: str, body: InsightPatchIn,
                  user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    """Accept / edit / reject one candidate. Accept and edit both promote
    into ProductProfile via the additive merge; reject does not. Forbidden
    fields (the *_override list) can never promote — promote_insight
    raises and we mark the row rejected as defense in depth."""
    insight = db.execute(
        scoped(ExtractedInsight, user.org_id)
        .where(ExtractedInsight.id == insight_id)
    ).scalar_one_or_none()
    if insight is None:
        raise HTTPException(404, "Insight not found")
    if insight.status != "pending":
        raise HTTPException(409, f"Insight already {insight.status}")
    action = (body.action or "").lower().strip()
    if action == "reject":
        insight.status = "rejected"
        insight.updated_at = _now()
        db.commit()
        return _serialize_insight(insight)
    if action not in ("accept", "edit"):
        raise HTTPException(400, "action must be 'accept', 'edit', or 'reject'")
    # An edit supplies the user's value; accept uses the original LLM value.
    if action == "edit":
        if body.value is None:
            raise HTTPException(400, "action='edit' requires a 'value' body")
        insight.accepted_value = body.value
    else:
        insight.accepted_value = insight.value
    insight.status = "accepted" if action == "accept" else "edited"

    product = db.execute(
        scoped(ProductProfile, user.org_id)
        .where(ProductProfile.id == insight.product_id)
    ).scalar_one()
    try:
        result = promote_insight(product, insight)
    except ValueError as exc:
        # Defense-in-depth: a forbidden field reaching this point means
        # the prompt + extract.py filter both failed. Refuse cleanly.
        insight.status = "rejected"
        insight.updated_at = _now()
        db.commit()
        raise HTTPException(400, f"Promotion refused: {exc}")
    product.updated_at = _now()
    insight.updated_at = _now()
    db.commit()
    db.refresh(insight)
    db.refresh(product)
    return {"insight": _serialize_insight(insight),
            "promotion": result}


# ---- Field history restore ----------------------------------------------
class HistoryRestoreIn(BaseModel):
    history_index: int


@products_doc_router.post("/{product_id}/field-history/restore")
def restore_field_history(product_id: str, body: HistoryRestoreIn,
                          user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict:
    """Re-activate a superseded history entry. The current active value
    for that field becomes superseded so there's always exactly one
    active value at a time."""
    product = _get_product(db, user, product_id)
    try:
        result = restore_history_entry(product, body.history_index)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    product.updated_at = _now()
    db.commit()
    db.refresh(product)
    return result
