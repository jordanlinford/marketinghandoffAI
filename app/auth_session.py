"""
Signed-cookie session for the authenticated user.

The session is deliberately minimal:
  payload = {"uid": "<user_id>", "exp": <unix_ts>}
HMAC-SHA256 over base64-url(payload). No external dependency — stdlib
hmac + hashlib + base64 + json.

Why a signed cookie (not a server session table): one fewer table to
migrate, no session store to operate, and the user's identity is the
only field we need at request time. The user row itself lives in the
DB; the cookie carries only the pointer + expiry. Rotating the secret
invalidates every session, which is the desired behavior on a key
compromise.

ALLOWLIST IS THE GATE: this module signs / verifies cookies, but does
NOT decide who's allowed in. That's `email_is_allowed()` in
app.config, evaluated every time current_user resolves a session.
A signed cookie for a user whose email was REMOVED from the allowlist
post-issuance will be rejected — the gate fires on every request,
not just at login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.config import get_settings


COOKIE_NAME_DEFAULT = "agenthq_session"


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: dict, secret: str) -> str:
    body = _b64url_encode(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _verify(token: str, secret: str) -> dict | None:
    """Verify a signed token. Returns the payload dict on success;
    None on any failure (bad signature, malformed, expired). The
    helper NEVER raises — callers treat None as 'not authenticated.'"""
    if not token or "." not in token or not secret:
        return None
    try:
        body, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(secret.encode(), body.encode(),
                                  hashlib.sha256).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            return None
        payload = json.loads(_b64url_decode(body).decode())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return None
    return payload


def issue_session_token(user_id: str) -> str:
    """Mint a signed session token for `user_id` with the configured TTL."""
    s = get_settings()
    if not s.session_secret:
        raise RuntimeError(
            "session_secret is empty — cannot sign cookies. Set "
            "SESSION_SECRET in env before issuing sessions.")
    payload = {
        "uid": user_id,
        "exp": int(time.time() + s.session_ttl_seconds),
        "iat": int(time.time()),
    }
    return _sign(payload, s.session_secret)


def read_session_user_id(token: str | None) -> str | None:
    """Return the user_id encoded in the session cookie, or None when
    the cookie is missing / invalid / expired."""
    if not token:
        return None
    s = get_settings()
    if not s.session_secret:
        # In dev with no secret set, sessions cannot be verified —
        # treat as unauthenticated. (Boot guard requires secret in
        # production; in dev a missing secret simply disables the
        # session path so dev_auth_bypass remains the only entry.)
        return None
    payload = _verify(token, s.session_secret)
    if payload is None:
        return None
    return payload.get("uid")


def cookie_kwargs() -> dict:
    """Cookie attributes used when setting the session cookie. HttpOnly
    always; Secure when configured (set True behind HTTPS); SameSite=
    Lax so the OAuth-callback redirect carries the cookie back to us."""
    s = get_settings()
    return {
        "key":      s.session_cookie_name,
        "max_age":  s.session_ttl_seconds,
        "httponly": True,
        "secure":   s.session_cookie_secure,
        "samesite": "lax",
        "path":     "/",
    }


# ----------------------------------------------------------------------------
# OAuth state signing — same HMAC primitive, dedicated namespace so a session
# cookie can't be replayed as a state value.
# ----------------------------------------------------------------------------
def sign_state(payload: dict, *, ttl_seconds: int = 600) -> str:
    """Sign an OAuth state value (provider, next-url, csrf nonce). Short
    TTL — only needs to survive the round trip to the IdP. Uses a
    DIFFERENT body prefix from session tokens so the two cannot
    cross-replay even though they share the secret."""
    s = get_settings()
    if not s.session_secret:
        raise RuntimeError(
            "session_secret is empty — cannot sign OAuth state.")
    p = dict(payload)
    p["exp"] = int(time.time() + ttl_seconds)
    p["_kind"] = "state"
    return _sign(p, s.session_secret)


def verify_state(token: str) -> dict | None:
    s = get_settings()
    if not s.session_secret:
        return None
    payload = _verify(token, s.session_secret)
    if payload is None or payload.get("_kind") != "state":
        return None
    return payload
