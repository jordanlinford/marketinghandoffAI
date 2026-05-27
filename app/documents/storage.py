"""
Tenant-scoped on-disk storage for raw product documents.

Layout:
  {settings.storage_root}/{org_id}/{product_id}/{doc_id}_{filename}

The org_id is part of the path so a scoped() lookup error can't lead to
serving the wrong tenant's file: even if the wrong row got loaded, the
path would be elsewhere. Smoke uses AGENT_HQ_STORAGE_ROOT in a tempdir
so test data never touches ./storage. ./storage is gitignored.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import get_settings


# Filenames are stored as-is in the DB, but on disk we sanitize aggressively
# to dodge path traversal + cross-platform footguns. Original filename is
# always available in ProductDocument.filename for display.
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str) -> str:
    base = os.path.basename(filename or "upload")
    sanitized = _SAFE_FILENAME.sub("_", base).strip("._-") or "upload"
    return sanitized[:200]


def storage_root() -> Path:
    return Path(get_settings().storage_root).resolve()


def doc_storage_path(org_id: str, product_id: str, doc_id: str,
                     filename: str) -> Path:
    """Build the absolute path for a raw upload. Caller is responsible for
    creating parent dirs (write_document below does it)."""
    if not org_id or not product_id or not doc_id:
        raise ValueError("doc_storage_path requires org_id, product_id, doc_id")
    return (storage_root() / org_id / product_id
            / f"{doc_id}_{_safe_filename(filename)}")


def write_document(org_id: str, product_id: str, doc_id: str,
                   filename: str, content: bytes) -> Path:
    """Persist the raw bytes under the tenant path and return the absolute
    path. Creates the directory tree if needed."""
    path = doc_storage_path(org_id, product_id, doc_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def read_document(path_str: str) -> bytes:
    """Read raw bytes for a stored doc. Callers should already have a
    scoped() row that gave them this path — we don't validate here, we
    just open. Path traversal protection is the storage layout (the path
    came out of doc_storage_path)."""
    return Path(path_str).read_bytes()
