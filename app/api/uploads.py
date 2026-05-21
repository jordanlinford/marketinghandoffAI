"""
CSV upload endpoints. Tenant-scoped — every read/write of an `uploads` row goes
through `scoped(Upload, org_id)`. Header mapping is forgiving (case-, space-
and underscore-insensitive), drops unmapped columns, and never crashes on a
missing or unparseable numeric. Revenue may be a range string like "$1M-$5M"
or "1,000,000-5,000,000" and is collapsed to a midpoint. Intent is honored
only when the CSV actually carries it (the data source then tags itself
"csv+intent"); otherwise intent stays 0.0 and we surface the unavailability.
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.data_sources.csv import _coerce_int, _parse_intent, _parse_revenue
from app.models import Upload, User
from app.tenancy import scoped

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _norm_header(h: str | None) -> str:
    """Lowercase, strip, treat '_' / '-' / '.' as spaces, collapse whitespace.
    'Business_Name', 'business name', 'BUSINESS-NAME' all normalize the same."""
    s = (h or "").strip().lower()
    for ch in ("_", "-", "."):
        s = s.replace(ch, " ")
    return " ".join(s.split())


# Normalized header → CompanyRecord field. Keys are already in normalized form.
_HEADER_ALIASES: dict[str, str] = {
    # name
    "name": "name", "company": "name", "company name": "name",
    "business name": "name", "account": "name", "account name": "name",
    "organization": "name", "org": "name",
    # employees
    "employees": "employees", "employee count": "employees",
    "# employees": "employees", "headcount": "employees",
    "size": "employees", "company size": "employees", "staff": "employees",
    # revenue (may be a number, a $5M-style token, or a "1M-5M" range)
    "revenue": "revenue_usd", "annual revenue": "revenue_usd",
    "revenue usd": "revenue_usd", "annual revenue usd": "revenue_usd",
    "annual revenue (usd)": "revenue_usd", "revenue ($)": "revenue_usd",
    "revenue range": "revenue_usd", "est revenue": "revenue_usd",
    "estimated revenue": "revenue_usd", "arr": "revenue_usd",
    # industry
    "industry": "industry", "sector": "industry", "vertical": "industry",
    # intent (presence flips the source label to "csv+intent")
    "intent": "intent_score", "intent score": "intent_score",
}


def _map_headers(fieldnames: list[str] | None) -> dict[str, str]:
    """Return {original_header: canonical_field}. Unmapped headers are dropped."""
    out: dict[str, str] = {}
    for h in fieldnames or []:
        canonical = _HEADER_ALIASES.get(_norm_header(h))
        if canonical:
            out[h] = canonical
    return out


def _name_alias_hint() -> str:
    return ", ".join(sorted({k for k, v in _HEADER_ALIASES.items() if v == "name"}))


def _parse_csv(raw: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw))
    mapping = _map_headers(reader.fieldnames)
    if "name" not in mapping.values():
        seen = ", ".join(reader.fieldnames or []) or "(no headers)"
        raise HTTPException(
            400,
            f"No name column found. Saw: {seen}. "
            f"Accepted name aliases: {_name_alias_hint()}.",
        )
    has_intent = "intent_score" in mapping.values()
    rows: list[dict] = []
    for raw_row in reader:
        canonical: dict = {}
        for src, dest in mapping.items():
            canonical[dest] = raw_row.get(src)
        name = (canonical.get("name") or "").strip()
        if not name:
            continue  # skip blank rows quietly; they're not data
        row = {
            "name": name,
            "employees": _coerce_int(canonical.get("employees")),
            "revenue_usd": _parse_revenue(canonical.get("revenue_usd")),
            "industry": (canonical.get("industry") or "").strip(),
        }
        # Only stamp intent_score when the column actually existed — its
        # presence is what flips CsvMarketDataSource into "csv+intent" mode.
        if has_intent:
            row["intent_score"] = _parse_intent(canonical.get("intent_score"))
        rows.append(row)
    return rows


def _serialize(u: Upload) -> dict:
    return {
        "id": u.id, "filename": u.filename, "row_count": u.row_count,
        "uploaded_by": u.uploaded_by, "created_at": u.created_at.isoformat(),
    }


@router.post("")
async def create_upload(file: UploadFile = File(...),
                        user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8-sig")  # tolerate BOM from Excel exports
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")
    rows = _parse_csv(text)
    if not rows:
        raise HTTPException(400, "CSV had a header but no data rows")
    up = Upload(org_id=user.org_id, filename=file.filename or "upload.csv",
                uploaded_by=user.id, row_count=len(rows), rows=rows)
    db.add(up)
    db.commit()
    db.refresh(up)
    return {**_serialize(up), "preview": rows[:5]}


@router.get("")
def list_uploads(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        scoped(Upload, user.org_id).order_by(Upload.created_at.desc())
    ).scalars().all()
    return [_serialize(u) for u in rows]
