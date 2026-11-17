"""Typed application settings loaded from the environment."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

TEST_WORKSPACE_ID = UUID("42132f25-4948-45c0-9142-8def637b0a9c")


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
    database_url: SecretStr = Field(
        default=SecretStr(
            "postgresql+psycopg://ledgerpilot_app:ambient-secret-never-used@localhost:5432/ledgerpilot"
        )
    )
    session_signing_key: SecretStr = Field(
        default=SecretStr("0123456789abcdef0123456789abcdef0123456789abcdef")
    )
    frontend_origins: str = "http://localhost:5173"
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_starter_price_id: str = ""
    stripe_growth_price_id: str = ""
    stripe_enterprise_price_id: str = ""
    billing_return_url: str = ""
    quickbooks_client_id: str = ""
    quickbooks_client_secret: SecretStr | None = None
    quickbooks_redirect_uri: str = ""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    api_replicas: int = Field(default=2, ge=1, le=100)
    worker_replicas: int = Field(default=2, ge=1, le=50)
    worker_pool_size: int = Field(default=5, ge=1, le=20)
    admin_db_headroom: int = Field(default=10, ge=2, le=50)
    enforce_connection_budget: bool = True
    provider_concurrency: int = Field(default=4, ge=1, le=32)
    provider_breaker_failures: int = Field(default=5, ge=1, le=100)
    provider_breaker_cooldown_seconds: int = Field(default=30, ge=1, le=600)
    api_max_in_flight: int = Field(default=64, ge=1, le=1024)
    heavy_max_in_flight: int = Field(default=2, ge=1, le=32)
    request_body_timeout_seconds: float = Field(default=30, gt=0, le=120)
    report_cache_redis_url: SecretStr | None = None
    report_cache_ttl_seconds: int = Field(default=10, ge=0, le=60)
    rate_redis_url: SecretStr | None = None
    rate_redis_connections: int = Field(default=24, ge=1, le=256)
    rate_redis_timeout_seconds: float = Field(default=0.25, gt=0, le=2)
    rate_db_pool_size: int = Field(default=2, ge=1, le=4)
    rate_user_budget: int = Field(default=240, ge=1, le=1000000)
    rate_workspace_budget: int = Field(default=600, ge=1, le=1000000)
    rate_workspace_member_budget: int = Field(default=120, ge=1, le=1000000)
    rate_auth_ip_budget: int = Field(default=60, ge=1, le=1000000)
    rate_auth_identity_budget: int = Field(default=5, ge=1, le=1000000)
    rate_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_cooldown_max_seconds: int = Field(default=60, ge=1, le=3600)
    jobs_default_max_attempts: int = Field(default=5, ge=1, le=20)
    jobs_default_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    jobs_poll_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    jobs_worker_concurrency: int = Field(default=4, ge=1, le=32)
    stripe_webhook_async: bool = True
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
    evidence_storage_backend: Literal["filesystem", "s3"] = "filesystem"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "ledgerpilot-evidence"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_use_ssl: bool = True
    evidence_max_bytes: int = Field(default=26214400, ge=1, le=26214400)
    clamav_host: str = "127.0.0.1"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    ocr_enabled: bool = False
    ocr_service_url: str = "http://127.0.0.1:8071/extract"
    ocr_service_token: SecretStr | None = None
    google_client_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$"
    )
    cookie_samesite: Literal["lax", "none"] = "lax"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    mailpit_ui_port: int = Field(default=8025, ge=1, le=65535)
    smtp_from: str = "accounts@ledgerpilot.local"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    auth_delivery_enabled: bool = False
    p2_email_delivery_enabled: bool = False
    p2_email_delivery_business_ids: frozenset[UUID] = frozenset()
    p2_project_business_ids: frozenset[UUID] = frozenset()
    p2_insight_business_ids: frozenset[UUID] = frozenset()
    p2_reconciliation_business_ids: frozenset[UUID] = frozenset()
    p2_prepaid_business_ids: frozenset[UUID] = frozenset()
    p2_micro_close_business_ids: frozenset[UUID] = frozenset()
    export_snapshot_retention_hours: int = Field(default=72, ge=1, le=720)
    p2_payload_disposal_enabled: bool = False
    p2_export_disposal_enabled: bool = False
    p2_export_disposal_approval: str = Field(default="", max_length=200)
    p2_retention_business_ids: frozenset[UUID] = frozenset()
    p2_audit_business_ids: frozenset[UUID] = frozenset()

    @field_validator(
        "p2_email_delivery_business_ids",
        "p2_project_business_ids",
        "p2_prepaid_business_ids",
        "p2_insight_business_ids",
        "p2_reconciliation_business_ids",
        "p2_micro_close_business_ids",
        "p2_retention_business_ids",
        "p2_audit_business_ids",
        mode="before",
    )
    @classmethod
    def parse_rollout_business_ids(cls, value: object) -> object:
        # FileSettings passes raw strings to the initializer. Normal environment
        # loading already decodes the JSON list before this validator runs.
        return json.loads(value) if isinstance(value, str) else value

    def allows_p2_email_delivery(self, business_id: UUID) -> bool:
        """Evaluate rollout for a business whose scope was already verified."""
        return (
            self.app_env != "demo"
            and (self.p2_email_delivery_enabled or business_id == TEST_WORKSPACE_ID)
            and (
                business_id == TEST_WORKSPACE_ID
                or business_id in self.p2_email_delivery_business_ids
            )
        )

    def allows_p2_projects(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_project_business_ids
        )

    def allows_p2_insights(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_insight_business_ids
        )

    def allows_p2_reconciliation(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_reconciliation_business_ids
        )

    def allows_p2_prepaids(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_prepaid_business_ids
        )

    def allows_p2_micro_close(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_micro_close_business_ids
        )

    def allows_p2_retention(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_retention_business_ids
        )

    def allows_p2_audit(self, business_id: UUID) -> bool:
        return self.app_env != "demo" and (
            business_id == TEST_WORKSPACE_ID or business_id in self.p2_audit_business_ids
        )

    @field_validator("session_signing_key")
    @classmethod
    def validate_signing_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if len(key.encode()) < 32 or key.lower().startswith(("replace-", "local-only-")):
            raise ValueError("Set a random signing secret of at least 32 bytes")
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        raw = raw.strip().strip("'\"")
        if raw.startswith("postgres://"):
            raw = "postgresql+psycopg://" + raw[len("postgres://") :]
        elif raw.startswith("postgresql://"):
            raw = "postgresql+psycopg://" + raw[len("postgresql://") :]
        return SecretStr(raw)

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
                and set(url.query)
                <= {
                    "sslmode",
                    "sslrootcert",
                    "target_session_attrs",
                    "channel_binding",
                    "application_name",
                }
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
        is_railway = bool(
            os.environ.get("RAILWAY_PROJECT_ID")
            or os.environ.get("RAILWAY_ENVIRONMENT")
            or os.environ.get("RAILWAY_SERVICE_ID")
            or (
                url.host
                and any(
                    url.host.endswith(s)
                    for s in (".railway.internal", ".internal", ".rlwy.net", ".railway.app")
                )
            )
        )
        if url.username == "postgres" and (self.app_db_user == "postgres" or is_railway):
            pass
        elif url.username != self.app_db_user or self.app_db_user.lower() in {
            "postgres",
            "ledgerpilot",
            "ledgerpilot_migrator",
        }:
            raise ValueError("DATABASE_URL must use the restricted application database role")
        if self.rate_redis_url and not self.rate_redis_url.get_secret_value().startswith(
            ("redis://", "rediss://")
        ):
            raise ValueError("Rate limiting requires a Redis URL")
        if self.app_env == "production" and self.rate_redis_url is None and not is_railway:
            raise ValueError("Production requires a dedicated Redis admission service")
        if self.app_env not in {"local", "test"}:
            is_internal_cloud = is_railway or bool(
                url.host
                and (url.host.endswith(".railway.internal") or url.host.endswith(".internal"))
            )
            if not is_internal_cloud and url.query.get("sslmode") != "verify-full":
                raise ValueError("Hosted database connections require sslmode=verify-full")
            if not is_railway and any(
                urlsplit(origin).scheme != "https" for origin in self.allowed_origins
            ):
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
            type(self).validate_signing_key(self.session_previous_signing_key)
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

    @property
    def total_db_connection_budget(self) -> int:
        """Global connection budget across API replicas, worker replicas, and admin headroom."""
        per_api_replica = self.db_pool_size + self.db_max_overflow + self.rate_db_pool_size
        return (
            (self.api_replicas * per_api_replica)
            + (self.worker_replicas * self.worker_pool_size)
            + self.admin_db_headroom
        )


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""

    return Settings()
