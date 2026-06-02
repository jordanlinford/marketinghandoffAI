from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import (admin, artifacts, assets, auth_routes, brand, campaigns,
                     dashboard, derivatives, documents, fanouts,
                     memory as memory_api, products, profile, reports, review,
                     runs, uploads)
from app.config import get_settings
from app.db import create_all

settings = get_settings()

# ---- Boot guard: prod refuses DEV_AUTH_BYPASS=true ----------------------
# The dev header path and the dev_stub provider both check this flag at
# request time, but the strongest defense is refusing to start the app
# at all if a misconfiguration sneaks into production. A bypass-on prod
# deploy would leave the header backdoor open — the entire point of the
# OAuth build is to close it.
if settings.is_production and settings.dev_auth_bypass:
    raise RuntimeError(
        "Refusing to boot: APP_ENV=production but DEV_AUTH_BYPASS is "
        "True. The header backdoor must be off in production — set "
        "DEV_AUTH_BYPASS=false (or unset).")
if settings.is_production and not settings.session_secret:
    raise RuntimeError(
        "Refusing to boot: APP_ENV=production but SESSION_SECRET is "
        "unset. Cookies cannot be signed without it.")

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
app.include_router(derivatives.router)
app.include_router(fanouts.router)
app.include_router(auth_routes.router)

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
