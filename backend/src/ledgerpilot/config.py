"""Typed application settings loaded from the environment."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    """Runtime configuration; secrets must not be sent to the web client."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test", "demo", "staging", "production"] = "local"
    app_name: str = "LedgerPilot API"
    api_v1_prefix: str = "/api/v1"
    app_db_user: str = "ledgerpilot_app"
    database_url: SecretStr
    session_signing_key: SecretStr
    frontend_origins: str = "http://localhost:5173"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=15000, ge=100, le=60000)

    @field_validator("session_signing_key")
    @classmethod
    def validate_signing_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if len(key.encode()) < 32 or key.lower().startswith(("replace-", "local-only-")):
            raise ValueError("Set a random signing secret of at least 32 bytes")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+psycopg"
                and url.host
                and url.database
                and url.username
                and url.password
                and (url.port is None or 1 <= url.port <= 65535)
                # libpq query arguments can override authority credentials/host.
                # Only certificate settings are delegated to the connection URL.
                and set(url.query) <= {"sslmode", "sslrootcert"}
                and all(isinstance(item, str) for item in url.query.values())
            )
        except (ValueError, TypeError, ArgumentError):
            valid = False
        if not valid:
            raise ValueError("Set a complete PostgreSQL psycopg URL")
        return value

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/") or "?" in value or "#" in value:
            raise ValueError("API prefix must be an absolute path without a trailing slash")
        return value

    @field_validator("frontend_origins")
    @classmethod
    def validate_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",")]
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
            ):
                raise ValueError("Configure explicit frontend origins without paths or wildcards")
            try:
                _ = parsed.port
            except ValueError:
                raise ValueError("Frontend origin port is invalid") from None
        return ",".join(origins)

    @model_validator(mode="after")
    def validate_runtime_boundary(self) -> "Settings":
        url = make_url(self.database_url.get_secret_value())
        if url.username != self.app_db_user or self.app_db_user.lower() in {
            "postgres",
            "ledgerpilot",
            "ledgerpilot_migrator",
        }:
            raise ValueError("DATABASE_URL must use the restricted application database role")
        if self.app_env not in {"local", "test"}:
            if url.query.get("sslmode") != "verify-full":
                raise ValueError("Hosted database connections require sslmode=verify-full")
            if any(urlsplit(origin).scheme != "https" for origin in self.allowed_origins):
                raise ValueError("Hosted frontend origins require HTTPS")
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return self.frontend_origins.split(",")


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""

    return Settings()
