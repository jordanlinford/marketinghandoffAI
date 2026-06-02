"""
Microsoft Entra (Azure AD) OAuth provider.

Authorization-code flow over OpenID Connect:
  1. Redirect to https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
     with response_type=code, scope="openid email profile User.Read",
     state=<signed CSRF>.
  2. Microsoft redirects back to MS_REDIRECT_URI with code + state.
  3. POST to /oauth2/v2.0/token to exchange code for an access_token.
  4. Call Microsoft Graph /v1.0/me with the access_token to fetch the
     verified email + display name.

Why call Graph /me instead of decoding the id_token: the access_token
+ Graph round-trip is over TLS to a trusted endpoint; the email it
returns is authoritative. Decoding the id_token without verifying the
RS256 signature would be unsafe; verifying the signature pulls in
JWKS-fetch + key-caching machinery we don't need for this one trust
boundary. Graph /me is the simpler, narrower path.

Tenant choice (MS_TENANT):
  * "organizations" — only work/school accounts; excludes personal
    Microsoft accounts. Recommended for an internal-Onit deploy.
  * "common"        — any MS account (including consumer). Use only
                      if you want a broad audience.
  * "<guid>"        — single tenant (your Entra tenant id).
"""
from __future__ import annotations

import httpx

from app.auth_providers.base import Identity, OAuthProvider
from app.config import get_settings


_MS_BASE = "https://login.microsoftonline.com"
_GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
_SCOPES = ["openid", "email", "profile", "User.Read"]


class MicrosoftProvider(OAuthProvider):
    key = "microsoft"
    display_name = "Microsoft"

    def is_available(self) -> bool:
        s = get_settings()
        return bool(s.ms_client_id and s.ms_client_secret
                    and s.ms_tenant and s.ms_redirect_uri)

    def authorize_url(self, *, state: str) -> str:
        s = get_settings()
        if not self.is_available():
            raise RuntimeError(
                "Microsoft provider is not configured; set "
                "MS_CLIENT_ID / MS_CLIENT_SECRET / MS_TENANT / "
                "MS_REDIRECT_URI before using /auth/login?provider=microsoft.")
        from urllib.parse import urlencode
        params = {
            "client_id":     s.ms_client_id,
            "response_type": "code",
            "redirect_uri":  s.ms_redirect_uri,
            "response_mode": "query",
            "scope":         " ".join(_SCOPES),
            "state":         state,
            # prompt=select_account so users can switch tenants/accounts
            # rather than being silently signed into the last one used.
            "prompt":        "select_account",
        }
        return f"{_MS_BASE}/{s.ms_tenant}/oauth2/v2.0/authorize?{urlencode(params)}"

    def exchange_code(self, *, code: str) -> Identity:
        s = get_settings()
        if not self.is_available():
            raise RuntimeError("Microsoft provider not configured")
        token_url = f"{_MS_BASE}/{s.ms_tenant}/oauth2/v2.0/token"
        token_resp = httpx.post(
            token_url,
            data={
                "client_id":     s.ms_client_id,
                "client_secret": s.ms_client_secret,
                "code":          code,
                "redirect_uri":  s.ms_redirect_uri,
                "grant_type":    "authorization_code",
                "scope":         " ".join(_SCOPES),
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        if token_resp.status_code >= 400:
            raise RuntimeError(
                f"Microsoft token exchange failed: "
                f"{token_resp.status_code} {token_resp.text[:200]}")
        tok = token_resp.json()
        access_token = tok.get("access_token")
        if not access_token:
            raise RuntimeError("Microsoft token response missing access_token")
        # Fetch verified identity from Graph /me. TLS-trusted; the
        # email returned is authoritative without our needing to
        # validate the id_token's JWT signature.
        me_resp = httpx.get(
            _GRAPH_ME,
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            timeout=15.0,
        )
        if me_resp.status_code >= 400:
            raise RuntimeError(
                f"Microsoft Graph /me failed: "
                f"{me_resp.status_code} {me_resp.text[:200]}")
        me = me_resp.json()
        # Field selection — Graph returns several email-shaped fields;
        # prefer mail (the authoritative SMTP), fall back to
        # userPrincipalName (the sign-in name, usually an email).
        email = (me.get("mail") or me.get("userPrincipalName") or "").strip()
        if not email:
            raise RuntimeError(
                "Microsoft Graph /me returned no email — cannot "
                "resolve identity.")
        name = (me.get("displayName") or email.split("@")[0]).strip()
        provider_user_id = me.get("id") or ""
        return Identity(email=email.lower(), name=name,
                         provider=self.key,
                         provider_user_id=provider_user_id)
