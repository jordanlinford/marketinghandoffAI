"""
OAuth provider seam.

THE LOAD-BEARING DESIGN PRINCIPLE: the provider is JUST AN IDENTITY
SOURCE. The allowlist is the gate. Every provider implements this
same three-method interface; current_user() never knows which
provider produced a session, because it doesn't matter — every
session's email goes through email_is_allowed() before becoming a
User.

Adding a second provider (Google later) is a new file in this
package + one registry entry — NOT a rewrite of current_user, NOT a
rewrite of any of the 91 routes that depend on it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    """The minimum verified identity every provider must return.
    email is the gate-relevant field; everything else is for the
    initial User row (name surfaces in the UI; provider_user_id is
    a stable identifier from the IdP for future cross-provider
    de-dup if we ever need it)."""
    email: str
    name: str
    provider: str            # "microsoft" / "google" / "dev_stub"
    provider_user_id: str    # provider-stable id (oid / sub / etc.)


class OAuthProvider:
    """Interface every provider implements. Three methods, that's the
    whole seam. The auth route layer is provider-agnostic — it only
    knows this interface.

    The implementations live in sibling files (microsoft.py,
    dev_stub.py, …) and register themselves in __init__.py.
    """

    # Stable string id used in URL routing (e.g. /auth/login?provider=microsoft).
    # Subclasses set this as a class attribute.
    key: str = ""

    # Human-visible name (used on login buttons).
    display_name: str = ""

    def authorize_url(self, *, state: str) -> str:
        """Build the IdP authorize URL the user is redirected to.
        `state` is the signed CSRF/round-trip value the callback will
        verify; pass it through verbatim."""
        raise NotImplementedError

    def exchange_code(self, *, code: str) -> Identity:
        """Exchange an authorization code for a verified Identity.
        Raises on any failure (network / bad code / expired). The
        callback layer treats any exception as 'auth failed' and
        does NOT establish a session."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Return True if this provider's required config is present.
        Lets the login UI show only providers that can actually run
        in the current environment (e.g. hide 'Microsoft' when
        MS_CLIENT_ID is unset on a local dev machine)."""
        raise NotImplementedError
