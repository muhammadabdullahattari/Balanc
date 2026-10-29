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
    rate_db_pool_size: int = Field(default=2, ge=1, le=4)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=15000, ge=100, le=60000)
    db_idle_transaction_timeout_ms: int = Field(default=15000, ge=5000, le=60000)
    jwt_issuer: str = "ledgerpilot"
    jwt_audience: str = "ledgerpilot-web"
    jwt_key_id: str = Field(default="v1", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    session_previous_signing_key: SecretStr | None = None
    session_previous_key_id: str | None = None
    mfa_encryption_key: SecretStr | None = None
    evidence_encryption_key: SecretStr | None = None
    export_signing_private_key: SecretStr | None = None
    evidence_storage_root: Path = Path(__file__).resolve().parents[3] / "tmp" / "private-evidence"
    evidence_max_bytes: int = Field(default=26214400, ge=1, le=26214400)
    clamav_host: str = "127.0.0.1"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    ocr_enabled: bool = False
    ocr_service_url: str = "http://127.0.0.1:8071/extract"
    ocr_service_token: SecretStr | None = None
    cookie_samesite: Literal["lax", "none"] = "lax"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_from: str = "accounts@ledgerpilot.local"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    auth_delivery_enabled: bool = False

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
            if self.auth_delivery_enabled and not self.smtp_starttls:
                raise ValueError("Hosted identity mail requires SMTP TLS")
        if self.mfa_encryption_key:
            from cryptography.fernet import Fernet

            try:
                Fernet(self.mfa_encryption_key.get_secret_value().encode())
            except (ValueError, TypeError):
                raise ValueError("MFA encryption key must be a valid Fernet key") from None
        if self.evidence_encryption_key:
            from cryptography.fernet import Fernet

            try:
                Fernet(self.evidence_encryption_key.get_secret_value().encode())
            except (ValueError, TypeError):
                raise ValueError("Evidence encryption key must be a valid Fernet key") from None
            if self.evidence_encryption_key in {
                self.mfa_encryption_key,
                self.session_signing_key,
            }:
                raise ValueError("Evidence, MFA and signing keys must be distinct")
        if self.export_signing_private_key:
            try:
                raw = bytes.fromhex(self.export_signing_private_key.get_secret_value())
            except ValueError:
                raise ValueError("Export signing key must be 32 bytes encoded as hex") from None
            if len(raw) != 32:
                raise ValueError("Export signing key must be 32 bytes encoded as hex")
        ocr_url = urlsplit(self.ocr_service_url)
        if (
            ocr_url.scheme != "http"
            or ocr_url.hostname not in {"127.0.0.1", "localhost", "ocr"}
            or ocr_url.path != "/extract"
            or ocr_url.username
            or ocr_url.password
            or ocr_url.query
            or ocr_url.fragment
        ):
            raise ValueError("OCR must use the configured local private extraction endpoint")
        if self.ocr_enabled and (
            self.ocr_service_token is None or len(self.ocr_service_token.get_secret_value()) < 32
        ):
            raise ValueError("Local OCR requires a private service token of at least 32 characters")
        if bool(self.session_previous_signing_key) != bool(self.session_previous_key_id):
            raise ValueError("Previous JWT key and key ID must be configured together")
        if self.session_previous_key_id == self.jwt_key_id:
            raise ValueError("JWT key identifiers must be distinct")
        if self.session_previous_signing_key:
            self.validate_signing_key(self.session_previous_signing_key)
        if self.auth_delivery_enabled and not self.mfa_encryption_key:
            raise ValueError("Identity features require a dedicated MFA encryption key")
        if self.mfa_encryption_key and (
            self.mfa_encryption_key.get_secret_value()
            == self.session_signing_key.get_secret_value()
        ):
            raise ValueError("MFA encryption and signing keys must be distinct")
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return self.frontend_origins.split(",")


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""

    return Settings()
