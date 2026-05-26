from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import admin, artifacts, profile, review, runs, uploads
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

_UI_FILE = Path(__file__).parent / "static" / "ui.html"


@app.on_event("startup")
def _startup():
    if settings.is_sqlite:  # dev convenience; prod runs sql/schema.sql explicitly
        create_all()


@app.get("/healthz")
def healthz():
    return {"ok": True, "env": settings.app_env}


@app.get("/ui")
def ui():
    return FileResponse(_UI_FILE, media_type="text/html")
