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

    dev_auth_bypass: bool = True
    dev_auth_email: str = "jordan@onit.com"
    allowed_email_domains: str = "onit.com"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    worker_poll_seconds: float = 2.0

    @property
    def allowed_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
