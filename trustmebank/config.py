"""Application settings, loaded from environment variables (and a local .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://trustmebank:trustmebank@localhost:5432/trustmebank"
    secret_key: str = "dev-only-change-me"
    admin_password: str = "admin"
    public_base_url: str = "http://localhost:8000"

    seed_on_startup: bool = True
    allow_negative_balances: bool = False

    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 30 * 24 * 3600
    auth_code_ttl_seconds: int = 300
    consent_ttl_days: int = 90

    payment_processing_delay_seconds: int = 0
    webhook_worker_enabled: bool = True
    worker_poll_interval_seconds: float = 2.0
    webhook_timeout_seconds: float = 10.0

    session_cookie_secure: bool = False
    log_level: str = "INFO"

    # Rate limits (requests per minute). 0 disables.
    rate_limit_api_per_minute: int = 300
    rate_limit_login_per_minute: int = 20
    rate_limit_token_per_minute: int = 60

    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
