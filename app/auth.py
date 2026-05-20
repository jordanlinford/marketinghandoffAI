"""
Auth + current-user resolution.

DEV (default): trust the `X-Dev-User-Email` header, or fall back to
DEV_AUTH_EMAIL. This lets you build without standing up OAuth.

PROD: replace `current_user` with Google Workspace SSO. Tenancy is always
driven by the org derived from the verified domain, never by anything the
client sends.

NOTE: the raw `select(User)` here is a legitimate exception to the scoped()
rule — we must resolve who the user IS before we can know their org_id.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User


def current_user(
    db: Session = Depends(get_db),
    x_dev_user_email: str | None = Header(default=None),
) -> User:
    settings = get_settings()
    if not settings.dev_auth_bypass:
        raise HTTPException(status_code=501, detail="SSO not configured in this build")

    email = (x_dev_user_email or settings.dev_auth_email).lower()
    domain = email.split("@")[-1]
    if domain not in settings.allowed_domains:
        raise HTTPException(status_code=403, detail=f"Domain '{domain}' not allowed")

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail=f"No user for {email} (run scripts/seed.py)")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
