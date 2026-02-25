from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # Application
    app_env: str = "development"
    secret_key: str = ""
    cors_origins: list[str] = ["http://localhost:3000"]

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_db_url: str = ""

    # Meta Lead Ads
    meta_verify_token: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_page_id: str = ""

    # Defaults
    default_due_days: int = 3
    max_call_attempts: int = 6
    csv_max_size_mb: int = 10
    csv_max_rows: int = 5000
    log_level: str = "INFO"

    @property
    def async_database_url(self) -> str:
        url = self.supabase_db_url
        # Strip query params (like ?pgbouncer=true) — not valid for asyncpg
        if "?" in url:
            url = url.split("?")[0]
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
