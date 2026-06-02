"""
Auth + current-user resolution.

THE SHAPE this file holds: `current_user()` is the ONE FastAPI
dependency that gates every authenticated route. All 91 call sites
depend on it. The implementation has been rewritten to support real
OAuth (Microsoft today, Google later via the provider seam in
app/auth_providers/), but the SIGNATURE is unchanged — no call site
needs editing.

Three-step resolution per request:
  1. Try the session cookie (signed via app/auth_session.py). If a
     valid uid is present AND the user's email STILL passes the
     allowlist, return that User.
  2. In non-prod with DEV_AUTH_BYPASS=true, fall back to the
     X-Dev-User-Email header. The header path is REQUIRED by the
     smoke (TestClient + headers); in prod it is unavailable.
  3. Otherwise → 401. NO fallback to a hardcoded default email.

The ALLOWLIST IS THE GATE on every step. A session cookie for a user
whose email got removed from the allowlist after issuance is
rejected. A header email that isn't allowlisted is rejected.
Provider authentication is necessary but not sufficient — the gate
decides. This is the seam that makes "add Google later" safe:
provider widens, allowlist still gates.

NOTE: the raw `select(User)` here is a legitimate exception to the
scoped() rule — we must resolve who the user IS before we can know
their org_id. Same rationale CLAUDE.md documents.

CLAUDE.md gap (open — this build does NOT close it): RLS via
`SET app.current_org` is required before opening this surface to
UNTRUSTED users. Allowlist-gated trusted users are fine without it
(the app-layer scoped() guard remains live).
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth_session
from app.config import email_is_allowed, get_settings
from app.db import get_db
from app.models import User


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    x_dev_user_email: str | None = Header(default=None),
) -> User:
    settings = get_settings()

    # ---- 1. Real session (the prod path) ---------------------------
    cookie = request.cookies.get(settings.session_cookie_name)
    user_id = auth_session.read_session_user_id(cookie) if cookie else None
    if user_id:
        user = db.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        if user is not None:
            # Re-check the allowlist on EVERY request. An email
            # removed from the allowlist post-issuance must lose
            # access immediately; the cookie is not enough.
            if not email_is_allowed(user.email):
                raise HTTPException(
                    403, f"Email {user.email!r} is no longer on the "
                    "allowlist for this deployment.")
            return user
        # Session pointed at a user_id that no longer exists — fall
        # through to other paths rather than 500. Caller will see 401
        # if no other path resolves.

    # ---- 2. Dev header path (local + smoke ONLY) -------------------
    # Available iff dev_auth_bypass is on AND not production. The
    # boot guard in app/main.py refuses to start the app with bypass
    # on in production, so step 2 is unreachable in a prod deploy.
    if settings.dev_auth_bypass and not settings.is_production:
        if x_dev_user_email:
            email = x_dev_user_email.strip().lower()
            if not email_is_allowed(email):
                raise HTTPException(
                    403, f"Email {email!r} is not on the allowlist "
                    "for this deployment.")
            user = db.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            if user is None:
                raise HTTPException(
                    401, f"No user for {email} (run scripts/seed.py "
                    "or sign in via /auth/login).")
            return user
        # bypass on, no header → fall through to 401. NO hardcoded
        # fallback email anymore. The brief calls this out
        # explicitly: 'no-header must NOT log anyone in.'

    # ---- 3. Unauthenticated --------------------------------------
    raise HTTPException(
        status_code=401,
        detail="Not authenticated — sign in at /auth/login.",
        headers={"WWW-Authenticate": "Cookie",
                 "Location": "/auth/login"},
    )


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
