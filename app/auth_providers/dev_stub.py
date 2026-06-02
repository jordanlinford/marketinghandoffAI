"""
Local development stub provider.

DEV-ONLY: this provider exists so the OAuth flow (signed-state round
trip, session-cookie issuance, allowlist gate) can be exercised
without real Microsoft credentials. It is registered ONLY when
settings.dev_auth_bypass is True; in production it is unreachable.

How it works (mimics the real OAuth dance shape):
  1. authorize_url() returns an internal URL that renders a small
     "pick an email" form. (The route lives in app/api/auth_routes.py
     under /auth/dev/picker; the provider just points the user there.)
  2. The form POSTs to /auth/dev/submit which validates the state and
     redirects to /auth/callback?provider=dev_stub&code=<email>&
     state=<state>. The CALLBACK is provider-agnostic — it asks
     dev_stub.exchange_code(code).
  3. exchange_code(code) treats the "code" as the chosen email and
     returns a verified Identity. The allowlist check happens AFTER
     this (in the callback) — proves "verified ≠ allowed."

The dev_stub does NOT bypass the allowlist. An email that isn't
allowlisted will still be rejected at the callback's gate check —
just as a real Microsoft sign-in would be. This is what makes the
dev stub useful for testing the GATE, not just the plumbing.
"""
from __future__ import annotations

from urllib.parse import urlencode

from app.auth_providers.base import Identity, OAuthProvider
from app.config import get_settings


class DevStubProvider(OAuthProvider):
    key = "dev_stub"
    display_name = "Dev stub (local only)"

    def is_available(self) -> bool:
        # Available iff dev_auth_bypass is on. In production this
        # returns False even if someone tried to flip the flag —
        # the boot guard refuses to start the app with bypass=on in
        # production, so this code path is unreachable in prod.
        s = get_settings()
        return bool(s.dev_auth_bypass and not s.is_production)

    def authorize_url(self, *, state: str) -> str:
        # Internal "picker" — the dev equivalent of the IdP authorize
        # page. State threads through verbatim, same as the real
        # providers; the picker validates it on submit.
        return f"/auth/dev/picker?{urlencode({'state': state})}"

    def exchange_code(self, *, code: str) -> Identity:
        # In the dev stub, the picker passes the chosen email AS the
        # code. Real providers exchange code for a token + identity;
        # here we skip that round-trip but produce the SAME Identity
        # shape the callback expects, so the path downstream of
        # exchange_code is exercised identically.
        email = (code or "").strip().lower()
        if not email or "@" not in email:
            raise RuntimeError(
                "dev_stub: invalid code (must be an email).")
        name = email.split("@", 1)[0]
        return Identity(email=email, name=name, provider=self.key,
                         provider_user_id=f"dev:{email}")
