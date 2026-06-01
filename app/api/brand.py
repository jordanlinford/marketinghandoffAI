"""
Tenant brand identity — strictly the PRESENTATION layer.

LOAD-BEARING DISCIPLINE: brand tokens feed chrome only (logo, color
roles, fonts). They MUST NOT enter the intelligence object, the
evidence ledger, or any input a §6/§7 validator reads. The smoke test
for this build pins the invariant: same scope rendered with brand
UNSET vs SET → body.content.blocks and body.trust_checks are
byte-identical. If brand can reach the claim layer, the build is
wrong.

One brand per org via UNIQUE org_id on the row. Unset is a valid
state: the GET endpoint returns sensible defaults so an unbranded
org still renders cleanly. Logo storage reuses the existing
storage_root layout under {storage_root}/{org_id}/_brand/.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.documents.storage import storage_root
from app.models import OrgBrand, User, _now, _uuid
from app.tenancy import scoped

router = APIRouter(prefix="/api/brand", tags=["brand"])


_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ALLOWED_LOGO_MIMES = {
    "image/png", "image/jpeg", "image/svg+xml", "image/webp", "image/gif",
}
_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB — plenty for a brand mark


# --------------------------------------------------------------------------
# Defaults — what an org sees when no row exists yet.
# Same values as the model defaults; mirrored here so the GET payload
# is a single source for the UI when no row is persisted.
# --------------------------------------------------------------------------
_DEFAULT_BRAND: dict[str, Any] = {
    "color_primary":    "#1f3b6b",
    "color_secondary":  "#2d8c5a",
    "color_accent":     "#c89a3a",
    "color_background": "#0e1218",
    "color_text":       "#e6e9f0",
    "font_heading":     "Inter",
    "font_body":        "Inter",
    "logo_path":        None,
    "logo_mime":        None,
}


def brand_for_org(db: Session, org_id: str) -> dict[str, Any]:
    """Pure helper — load the org's brand as a plain dict, falling
    back to defaults if no row exists. Callers that need to render
    presentation chrome use this (worker pre-load, API GET, etc.).
    NEVER returns None — unset is always a valid render state."""
    row = db.execute(
        scoped(OrgBrand, org_id)
    ).scalar_one_or_none()
    if row is None:
        return {**_DEFAULT_BRAND, "is_default": True, "org_id": org_id}
    return {
        "is_default": False,
        "org_id": row.org_id,
        "color_primary":    row.color_primary,
        "color_secondary":  row.color_secondary,
        "color_accent":     row.color_accent,
        "color_background": row.color_background,
        "color_text":       row.color_text,
        "font_heading":     row.font_heading,
        "font_body":        row.font_body,
        "logo_path":        row.logo_path,
        "logo_mime":        row.logo_mime,
    }


class BrandIn(BaseModel):
    color_primary:    str | None = Field(None)
    color_secondary:  str | None = Field(None)
    color_accent:     str | None = Field(None)
    color_background: str | None = Field(None)
    color_text:       str | None = Field(None)
    font_heading:     str | None = Field(None)
    font_body:        str | None = Field(None)


def _validate_hex(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not _HEX_RE.match(value):
        raise HTTPException(
            400, f"{field} must be a 7-char hex string like '#1f3b6b'")
    return value.lower()


@router.get("")
def get_brand(user: User = Depends(current_user),
              db: Session = Depends(get_db)) -> dict:
    return brand_for_org(db, user.org_id)


@router.put("")
def put_brand(body: BrandIn,
              user: User = Depends(current_user),
              db: Session = Depends(get_db)) -> dict:
    row = db.execute(scoped(OrgBrand, user.org_id)).scalar_one_or_none()
    if row is None:
        row = OrgBrand(id=_uuid(), org_id=user.org_id)
        db.add(row)

    # Apply each provided field with hex validation on colors.
    field_map = [
        ("color_primary",    True),
        ("color_secondary",  True),
        ("color_accent",     True),
        ("color_background", True),
        ("color_text",       True),
        ("font_heading",     False),
        ("font_body",        False),
    ]
    for field, is_hex in field_map:
        v = getattr(body, field)
        if v is None:
            continue
        if is_hex:
            v = _validate_hex(v, field)
        else:
            v = str(v).strip()[:120] or None
            if v is None:
                continue
        setattr(row, field, v)
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return brand_for_org(db, user.org_id)


def _logo_dir(org_id: str) -> Path:
    """{storage_root}/{org_id}/_brand/. Same layout discipline as
    product documents — org_id is part of the path so a scoped lookup
    error can't serve the wrong tenant's logo."""
    return storage_root() / org_id / "_brand"


_EXT_BY_MIME = {
    "image/png":    "png",
    "image/jpeg":   "jpg",
    "image/svg+xml": "svg",
    "image/webp":   "webp",
    "image/gif":    "gif",
}


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...),
                      user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    mime = (file.content_type or "").lower()
    if mime not in _ALLOWED_LOGO_MIMES:
        raise HTTPException(
            415, f"Unsupported logo mime type {mime!r}; "
                 f"expected one of {sorted(_ALLOWED_LOGO_MIMES)}.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty logo upload.")
    if len(content) > _MAX_LOGO_BYTES:
        raise HTTPException(413, f"Logo too large ({len(content)} bytes); "
                                   f"max {_MAX_LOGO_BYTES}.")

    ext = _EXT_BY_MIME.get(mime, "bin")
    logo_dir = _logo_dir(user.org_id)
    logo_dir.mkdir(parents=True, exist_ok=True)
    abs_path = logo_dir / f"logo.{ext}"
    abs_path.write_bytes(content)
    # Stored as a path RELATIVE to storage_root so callers don't need
    # to know the absolute filesystem layout.
    rel_path = abs_path.relative_to(storage_root()).as_posix()

    row = db.execute(scoped(OrgBrand, user.org_id)).scalar_one_or_none()
    if row is None:
        row = OrgBrand(id=_uuid(), org_id=user.org_id)
        db.add(row)
    row.logo_path = rel_path
    row.logo_mime = mime
    row.updated_at = _now()
    db.commit()
    return brand_for_org(db, user.org_id)


@router.get("/logo")
def serve_logo(user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> FileResponse:
    row = db.execute(scoped(OrgBrand, user.org_id)).scalar_one_or_none()
    if row is None or not row.logo_path:
        raise HTTPException(404, "No logo on file for this org.")
    abs_path = storage_root() / row.logo_path
    if not abs_path.is_file():
        raise HTTPException(404, "Logo file missing on disk.")
    return FileResponse(
        path=str(abs_path),
        media_type=row.logo_mime or "application/octet-stream",
        filename=os.path.basename(abs_path.name))


@router.delete("/logo")
def delete_logo(user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    row = db.execute(scoped(OrgBrand, user.org_id)).scalar_one_or_none()
    if row is None or not row.logo_path:
        return brand_for_org(db, user.org_id)
    abs_path = storage_root() / row.logo_path
    if abs_path.is_file():
        try:
            abs_path.unlink()
        except OSError:
            pass
    row.logo_path = None
    row.logo_mime = None
    row.updated_at = _now()
    db.commit()
    return brand_for_org(db, user.org_id)
