from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import (admin, artifacts, assets, brand, campaigns, dashboard,
                     documents, memory as memory_api, products, profile,
                     reports, review, runs, uploads)
from app.config import get_settings
from app.db import create_all

settings = get_settings()
app = FastAPI(title="Agent HQ", version="0.1.0")

app.include_router(runs.router)
app.include_router(review.router)
app.include_router(admin.router)
app.include_router(uploads.router)
app.include_router(profile.router)
app.include_router(artifacts.router)
app.include_router(dashboard.router)
app.include_router(products.router)
# Two routers for the document-ingestion API: one rooted at /api/products
# (per-product endpoints), one at /api/insights (per-id PATCH).
app.include_router(documents.products_doc_router)
app.include_router(documents.insights_router)
app.include_router(assets.router)
app.include_router(campaigns.router)
app.include_router(memory_api.router)
app.include_router(reports.router)
app.include_router(brand.router)

_UI_FILE = Path(__file__).parent / "static" / "ui.html"


@app.on_event("startup")
def _startup():
    if settings.is_sqlite:  # dev convenience; prod runs sql/schema.sql explicitly
        create_all()
    # One-time idempotent backfill: legacy reports (Artifact.type=
    # content_draft with body.content.content_type=report_*) get
    # promoted to type="report_draft" so the Library kind filter +
    # badge can resolve them off the top-level field. Runs every
    # startup; a no-op once everything's migrated.
    from app.db import SessionLocal
    from app.reports.backfill import backfill_report_artifact_type
    _db = SessionLocal()
    try:
        n = backfill_report_artifact_type(_db)
        if n:
            print(f"[startup] Backfilled report Artifact.type for {n} row(s).")
    finally:
        _db.close()


@app.get("/healthz")
def healthz():
    return {"ok": True, "env": settings.app_env}


@app.get("/ui")
def ui():
    return FileResponse(_UI_FILE, media_type="text/html")
