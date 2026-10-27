"""Typed application settings loaded from the environment."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets must not be sent to the web client."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    app_name: str = "LedgerPilot API"
    api_v1_prefix: str = "/api/v1"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://ledgerpilot:ledgerpilot_dev_only@localhost:5432/ledgerpilot"
    )
    session_signing_key: SecretStr = SecretStr("local-only-change-before-deploy")
    frontend_origins: str = "http://localhost:5173"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""

    return Settings()
