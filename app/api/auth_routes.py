"""
OAuth login + callback + logout routes.

Provider-agnostic core: every route below dispatches via the
OAuthProvider interface — the route layer never knows whether the
provider is Microsoft, Google (future), or the dev stub. Adding a
provider = a new entry in app/auth_providers/__init__.py + a button
on /auth/login. The auth route file does not change.

THE ALLOWLIST IS THE GATE: /auth/callback is where "verified
identity" meets "allowed to log in." A successful provider exchange
that doesn't pass email_is_allowed() is REJECTED with 403 — the
session cookie is never issued. This is what makes "add Google later"
safe: provider widens, allowlist still gates.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth_session
from app.auth_providers import available_providers, get_provider
from app.config import email_is_allowed, get_settings
from app.db import get_db
from app.models import Org, User

router = APIRouter(prefix="/auth", tags=["auth"])


# ---- /auth/login ---------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(provider: str | None = Query(None),
               next: str = Query("/ui")):
    """Show the provider chooser, OR redirect immediately when a
    specific provider is named. State (CSRF + the post-login redirect
    target) is signed so the callback can verify both."""
    s = get_settings()
    # Direct provider selection — skip the chooser.
    if provider:
        p = get_provider(provider)
        if p is None or not p.is_available():
            raise HTTPException(
                400, f"Provider {provider!r} not available in this "
                "environment.")
        state = auth_session.sign_state(
            {"provider": p.key, "next": next})
        return RedirectResponse(p.authorize_url(state=state),
                                  status_code=302)
    # Chooser — render minimal HTML listing every available provider.
    providers = available_providers()
    if not providers:
        return HTMLResponse(
            "<h2>No login provider configured</h2>"
            "<p>This deployment has no OAuth provider available. "
            "Set MS_CLIENT_ID / MS_CLIENT_SECRET / MS_TENANT / "
            "MS_REDIRECT_URI, or enable DEV_AUTH_BYPASS=true for "
            "local development.</p>",
            status_code=503)
    buttons = "\n".join(
        f'<a class="btn" href="/auth/login?provider={p.key}'
        f'&amp;next={next}">{p.display_name}</a>'
        for p in providers
    )
    dev_note = ("" if s.is_production else
                '<p class="muted">Dev mode — the dev stub provider is '
                'available for local testing.</p>')
    body = f"""
    <!DOCTYPE html><html><head><title>Sign in — Agent HQ</title>
    <style>
      body {{ font-family: system-ui, sans-serif; background:#0e1218;
              color:#e6e9f0; padding:60px; text-align:center; }}
      h1 {{ font-size:24px; margin-bottom:24px; }}
      .btn {{ display:inline-block; margin:6px; padding:12px 24px;
              background:#1f3b6b; color:#fff; text-decoration:none;
              border-radius:6px; font-weight:500; }}
      .btn:hover {{ background:#2d4a8b; }}
      .muted {{ color:#888; font-size:13px; margin-top:24px; }}
    </style></head><body>
    <h1>Sign in to Agent HQ</h1>
    {buttons}
    {dev_note}
    </body></html>
    """
    return HTMLResponse(body)


# ---- /auth/callback ------------------------------------------------------
@router.get("/callback")
def callback(request: Request,
             code: str | None = Query(None),
             state: str | None = Query(None),
             error: str | None = Query(None),
             error_description: str | None = Query(None),
             db: Session = Depends(get_db)):
    """The provider-agnostic callback. Every provider lands here; the
    `state` payload says which one did. ALLOWLIST GATE fires here —
    a verified identity whose email isn't allowed gets 403."""
    if error:
        raise HTTPException(
            400, f"OAuth provider returned an error: {error} "
                 f"({error_description or 'no detail'})")
    if not code or not state:
        raise HTTPException(400, "Missing code or state.")
    state_payload = auth_session.verify_state(state)
    if state_payload is None:
        raise HTTPException(
            400, "Invalid or expired state token — the login flow "
            "must be restarted.")
    provider_key = state_payload.get("provider", "")
    next_url = state_payload.get("next") or "/ui"
    p = get_provider(provider_key)
    if p is None or not p.is_available():
        raise HTTPException(
            400, f"Provider {provider_key!r} no longer available.")

    try:
        identity = p.exchange_code(code=code)
    except Exception as exc:
        raise HTTPException(401, f"Identity exchange failed: {exc!r}")

    # ---- THE GATE ----------------------------------------------------
    # Provider authentication succeeded — but that's necessary, not
    # sufficient. The ALLOWLIST decides who comes in.
    if not email_is_allowed(identity.email):
        raise HTTPException(
            403, f"Email {identity.email!r} is not on the allowlist "
            "for this deployment. Sign-in succeeded but you're not "
            "permitted access.")

    # ---- User row resolution / auto-provision ----------------------
    # Lookup-or-create the User row. The org is derived from the
    # email's domain (matching the tenancy discipline in CLAUDE.md
    # — the org is NEVER trusted from the client).
    domain = identity.email.rsplit("@", 1)[-1].lower()
    user = db.execute(
        select(User).where(User.email == identity.email)
    ).scalar_one_or_none()
    if user is None:
        # Find the org by its UNIQUE domain — that's the canonical
        # mapping (seeded as "onit.com" for the Onit org). Falls
        # back to creating a domain-named org for first-time sign-ins
        # from a new allowlisted domain. NEVER trust client-supplied
        # org_id; the email's verified domain is the only source.
        org = db.execute(
            select(Org).where(Org.domain == domain)
        ).scalar_one_or_none()
        if org is None:
            org = Org(name=domain.split(".")[0].title(), domain=domain)
            db.add(org)
            db.flush()
        user = User(org_id=org.id, email=identity.email,
                     name=identity.name, role="member")
        db.add(user)
        db.commit()
        db.refresh(user)

    # ---- Establish session + redirect ------------------------------
    token = auth_session.issue_session_token(user.id)
    resp = RedirectResponse(next_url, status_code=302)
    resp.set_cookie(value=token, **auth_session.cookie_kwargs())
    return resp


# ---- /auth/logout --------------------------------------------------------
@router.get("/logout")
def logout():
    s = get_settings()
    resp = RedirectResponse("/auth/login", status_code=302)
    resp.delete_cookie(s.session_cookie_name, path="/")
    return resp


# ---- /auth/me ------------------------------------------------------------
# Convenience for the UI to confirm "am I signed in?" without hitting a
# protected route first. Returns 401 cleanly when not authenticated.
@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    from app.auth import current_user  # local import: avoid cycle
    user = current_user(request, db, x_dev_user_email=None)
    return {"id": user.id, "email": user.email, "name": user.name,
            "org_id": user.org_id, "role": user.role}


# =========================================================================
# Dev stub picker — ONLY mounted when dev_auth_bypass is on. Real OAuth
# providers redirect to login.microsoftonline.com (etc.); the dev stub
# redirects users here, where they pick an email to "sign in" as. The
# picker then redirects back through the SAME /auth/callback path the
# real providers use, so the allowlist + session + user-row code is
# exercised end-to-end.
# =========================================================================
@router.get("/dev/picker", response_class=HTMLResponse)
def dev_picker(state: str = Query(...)):
    s = get_settings()
    if not s.dev_auth_bypass or s.is_production:
        raise HTTPException(404, "Dev picker not available.")
    # Verify the state up front — same protection a real IdP would
    # give us via signed assertion. Bad state = restart the flow.
    if auth_session.verify_state(state) is None:
        raise HTTPException(400, "Invalid or expired state token.")
    body = f"""
    <!DOCTYPE html><html><head><title>Dev sign in</title>
    <style>
      body {{ font-family: system-ui, sans-serif; background:#0e1218;
              color:#e6e9f0; padding:60px; text-align:center; }}
      input {{ padding:8px; border:1px solid #444; background:#1a1d24;
                color:#e6e9f0; border-radius:4px; width:280px; }}
      button {{ padding:8px 18px; background:#2d8c5a; color:#fff;
                 border:0; border-radius:4px; cursor:pointer; }}
      .muted {{ color:#888; font-size:13px; margin-top:18px; }}
    </style></head><body>
    <h2>Dev sign in (stub provider)</h2>
    <form method="post" action="/auth/dev/submit">
      <input type="hidden" name="state" value="{state}" />
      <p>Type the email to sign in as:</p>
      <input type="email" name="email" required autofocus />
      <p><button type="submit">Sign in</button></p>
    </form>
    <p class="muted">Allowlist still applies. An email not in the
    allowlist will be rejected at the callback, just like a real
    Microsoft sign-in would be.</p>
    </body></html>
    """
    return HTMLResponse(body)


@router.post("/dev/submit")
def dev_submit(state: str = Form(...), email: str = Form(...)):
    """Bridge from the dev picker form back into the
    provider-agnostic /auth/callback. We treat the chosen email as
    the OAuth 'code' — dev_stub.exchange_code(code) returns it
    verbatim as the verified Identity."""
    s = get_settings()
    if not s.dev_auth_bypass or s.is_production:
        raise HTTPException(404, "Dev submit not available.")
    if auth_session.verify_state(state) is None:
        raise HTTPException(400, "Invalid or expired state token.")
    from urllib.parse import urlencode
    return RedirectResponse(
        "/auth/callback?" + urlencode({"code": email, "state": state}),
        status_code=302)
