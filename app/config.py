from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    # Read from AGENT_HQ_DATABASE_URL (not DATABASE_URL) so we don't collide
    # with the existing TS app's Postgres, which has its own incompatible
    # `users` table. Defaults to SQLite for zero-setup dev.
    database_url: str = Field(default="sqlite:///./agenthq.db",
                              validation_alias="AGENT_HQ_DATABASE_URL")

    # ---- Auth ---------------------------------------------------------
    # dev_auth_bypass: when True, the X-Dev-User-Email header path AND
    # the dev_stub OAuth provider are both available. When False, ONLY
    # the real OAuth flow + session cookie are accepted. In production
    # the app refuses to boot with bypass=True (see app/main.py).
    dev_auth_bypass: bool = True
    # Domain allowlist (comma-separated). An email's domain must be in
    # this list OR the email itself must be in email_allowlist.
    allowed_email_domains: str = "onit.com"
    # Explicit-email allowlist (comma-separated). For named individuals
    # whose domain isn't in the domain allowlist (e.g. specific Gmail
    # addresses). The ALLOWLIST IS THE GATE — provider auth is necessary
    # but not sufficient.
    email_allowlist: str = ""

    # ---- Session ------------------------------------------------------
    # HMAC key for signed session cookies. Required in production (see
    # main.py boot guard). For local dev a stable default lets you
    # restart without losing sessions during testing.
    session_secret: str = ""
    session_ttl_seconds: int = 7 * 86400  # 7 days
    session_cookie_name: str = "agenthq_session"
    # Set True behind HTTPS (production). False for local http://.
    session_cookie_secure: bool = False

    # ---- OAuth providers ---------------------------------------------
    # Microsoft Entra. All required when the microsoft provider is used
    # in a non-dev environment; the dev_stub provider is the local
    # alternative when these are unset.
    ms_client_id: str = ""
    ms_client_secret: str = ""
    # Tenant — "common" / "organizations" / a specific GUID per the
    # Azure app registration. "common" accepts any work or school
    # account; "organizations" excludes consumer MS accounts.
    ms_tenant: str = "organizations"
    ms_redirect_uri: str = ""

    # ---- LLM ----------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    worker_poll_seconds: float = 2.0

    # Root for tenant-scoped document storage. Real uploads live under
    # {storage_root}/{org_id}/{product_id}/ — see app/documents/storage.py.
    # Overridden in smoke (AGENT_HQ_STORAGE_ROOT) to a tempdir so tests
    # never touch ./storage. Documents are gitignored.
    storage_root: str = Field(default="./storage",
                              validation_alias="AGENT_HQ_STORAGE_ROOT")

    @property
    def allowed_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def email_allowlist_set(self) -> set[str]:
        return {e.strip().lower() for e in self.email_allowlist.split(",") if e.strip()}

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def email_is_allowed(email: str) -> bool:
    """The ALLOWLIST gate — provider-agnostic. A verified identity is
    allowed in iff its email passes this check; provider auth is
    necessary but not sufficient. This is the seam that makes
    'add Google later' safe: provider widens, allowlist still gates."""
    s = get_settings()
    if not email or "@" not in email:
        return False
    email = email.lower().strip()
    if email in s.email_allowlist_set:
        return True
    domain = email.rsplit("@", 1)[-1]
    return domain in s.allowed_domains
