"""
Provider registry — the single place that knows which OAuth providers
exist. Add a new provider HERE; current_user, the callback route, and
the call sites all see it through the OAuthProvider interface, never
by name.
"""
from __future__ import annotations

from app.auth_providers.base import Identity, OAuthProvider
from app.auth_providers.dev_stub import DevStubProvider
from app.auth_providers.microsoft import MicrosoftProvider


# Instantiate the providers eagerly; they're stateless beyond config
# (which they re-read via get_settings on each call). Adding Google
# later = one line here + a new file alongside microsoft.py.
_PROVIDERS: dict[str, OAuthProvider] = {
    p.key: p for p in (MicrosoftProvider(), DevStubProvider())
}


def get_provider(key: str) -> OAuthProvider | None:
    return _PROVIDERS.get((key or "").strip().lower())


def available_providers() -> list[OAuthProvider]:
    """Providers that have the config they need to actually run. The
    login UI shows buttons only for these — never a button that
    would 500 on click because the env vars are missing."""
    return [p for p in _PROVIDERS.values() if p.is_available()]


__all__ = ["Identity", "OAuthProvider", "get_provider",
            "available_providers"]
