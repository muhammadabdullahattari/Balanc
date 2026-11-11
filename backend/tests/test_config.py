"""Configuration must fail safely before a misconfigured API starts."""

from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from ledgerpilot.config import Settings

URL = "postgresql+psycopg://ledgerpilot_app:configuration-test-password@localhost:5432/ledgerpilot"
KEY = "configuration-test-key-with-at-least-32-bytes"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "local",
        "database_url": SecretStr(URL),
        "session_signing_key": SecretStr(KEY),
        "frontend_origins": "http://localhost:5173",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///test.db",
        "not-a-url",
        "postgresql+psycopg://localhost/no-password",
        URL.replace(":5432/", ":-1/"),
        URL.replace(":5432/", ":100000/"),
        URL.replace("ledgerpilot_app", "postgres"),
        URL.replace("ledgerpilot_app", "ledgerpilot"),
        URL + "?user=postgres",
        URL + "?password=other-password",
        URL + "?host=remote.example.com",
        URL + "?dbname=other_database",
        URL + "?service=privileged",
        URL + "?options=-c%20role=postgres",
        URL + "?sslmode=verify-full&sslmode=disable",
    ],
)
def test_rejects_invalid_or_admin_database_url_without_leaking_it(url: str) -> None:
    with pytest.raises(ValidationError) as result:
        settings(database_url=SecretStr(url))
    assert url not in str(result.value)
    assert "configuration-test-password" not in str(result.value)


@pytest.mark.parametrize("key", ["short", "replace-with-a-random-secret-of-at-least-32-bytes"])
def test_rejects_missing_or_placeholder_signing_secret(key: str) -> None:
    with pytest.raises(ValidationError) as result:
        settings(session_signing_key=SecretStr(key))
    assert key not in str(result.value)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.example.com",
        "https://example.com/path",
        "https://user:pass@example.com",
        "",
        "http://example.com:bad",
    ],
)
def test_requires_explicit_valid_frontend_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        settings(frontend_origins=origin)


def test_hosted_configuration_requires_database_certificate_verification_and_https() -> None:
    with pytest.raises(ValidationError, match="sslmode"):
        settings(app_env="production")
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(app_env="demo", database_url=SecretStr(URL + "?sslmode=verify-full"))
    configured = settings(
        app_env="production",
        frontend_origins="https://app.example.com",
        database_url=SecretStr(URL + "?sslmode=verify-full"),
    )
    assert configured.allowed_origins == ["https://app.example.com"]


def test_pool_limits_and_secret_redaction() -> None:
    configured = settings()
    assert configured.db_pool_size + configured.db_max_overflow == 10
    assert KEY not in repr(configured)
    assert "configuration-test-password" not in repr(configured)
    with pytest.raises(ValidationError):
        settings(db_pool_size=0)


def test_p2_email_rollout_requires_switch_and_exact_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, other = uuid4(), uuid4()
    assert not settings().allows_p2_email_delivery(allowed)
    assert not settings(p2_email_delivery_enabled=True).allows_p2_email_delivery(allowed)
    assert not settings(p2_email_delivery_business_ids=[allowed]).allows_p2_email_delivery(allowed)

    monkeypatch.setenv("P2_EMAIL_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("P2_EMAIL_DELIVERY_BUSINESS_IDS", f'["{allowed}"]')
    configured = settings()
    assert configured.allows_p2_email_delivery(allowed)
    assert not configured.allows_p2_email_delivery(other)
    assert not settings(p2_email_delivery_enabled=False).allows_p2_email_delivery(allowed)
    assert not settings(
        app_env="demo",
        frontend_origins="https://app.example.com",
        database_url=SecretStr(URL + "?sslmode=verify-full"),
    ).allows_p2_email_delivery(allowed)


def test_p2_email_allowlist_accepts_file_json_and_rejects_wildcards() -> None:
    allowed = uuid4()
    assert settings(
        p2_email_delivery_enabled=True,
        p2_email_delivery_business_ids=f'["{allowed}"]',
    ).allows_p2_email_delivery(allowed)
    with pytest.raises(ValidationError):
        settings(p2_email_delivery_business_ids='["*"]')
    with pytest.raises(ValidationError):
        settings(p2_email_delivery_business_ids="not-json")
