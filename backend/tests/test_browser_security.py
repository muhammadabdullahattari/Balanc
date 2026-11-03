"""Focused password, cookie, CSRF and hosted-transport security acceptance."""

import io
import logging
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_phase2_integration import client, database_settings, new_identity  # noqa: F401

from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.identity.dependencies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from ledgerpilot.identity.models import AuthSession, RefreshToken, User
from ledgerpilot.identity.security import decode_access, digest, now, verify_password
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# ruff: noqa: F811 -- shared isolated service fixtures
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL/Mailpit"
)


async def test_hosted_http_rejected_before_body_even_with_forged_forwarding() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+psycopg://ledgerpilot_app:synthetic@localhost/ledgerpilot?sslmode=verify-full",
        session_signing_key="synthetic-security-test-key-at-least-32-bytes",
        frontend_origins="https://web.ledgerpilot.test",
    )
    app = create_app(settings)
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b'{"email":"synthetic@example.com","password":"Never log this phrase 7!"}'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.ledgerpilot.test"
    ) as api:
        reply = await api.post(
            "/api/v1/auth/login", content=body(), headers={"X-Forwarded-Proto": "https"}
        )
        assert reply.status_code == 403 and reply.json()["error"]["code"] == "ERR_HTTPS_REQUIRED"
        assert not consumed and "Never log" not in reply.text
        assert (await api.get("/health/live")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://api.ledgerpilot.test"
    ) as api:
        reply = await api.get("/api/v1/auth/google/config")
        assert reply.status_code == 200
        assert reply.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert set(reply.json()) == {"enabled", "client_id", "local_mailbox"}


@REAL
async def test_credentials_excluded_from_responses_logs_and_plain_storage(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    loggers = [logging.getLogger("ledgerpilot.requests"), logging.getLogger("ledgerpilot.errors")]
    for logger in loggers:
        logger.addHandler(handler)
    try:
        email, password, _ = await new_identity(client)
        access, refresh, csrf = (
            client.cookies.get(k) for k in [ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE]
        )
        assert access and refresh and csrf
        me = await client.get("/api/v1/auth/me")
        assert set(me.json()) == {"id", "email", "verified", "mfa"}
        reply = await client.post("/api/v1/auth/refresh")
        assert reply.status_code == 200 and reply.json() == {"status": "refreshed"}
        assert "no-store" in reply.headers["Cache-Control"]
        for cookie in reply.headers.get_list("Set-Cookie"):
            lowered = cookie.lower()
            assert (
                "httponly" in lowered
                and "secure" in lowered
                and "samesite=lax" in lowered
                and "path=/" in lowered
            )
            assert "domain=" not in lowered
        uid, sid = decode_access(database_settings, str(client.cookies.get(ACCESS_COOKIE)))
        engine = build_engine(database_settings)
        try:
            with Session(engine) as db, db.begin():
                set_context(db, uid)
                user = db.get(User, uid)
                family = db.get(AuthSession, sid)
                assert user and family and user.password_hash.startswith("$argon2id$")
                assert user.password_hash != password and verify_password(
                    user.password_hash, password
                )
                assert family.csrf_hash == digest(str(client.cookies.get(CSRF_COOKIE)))
                rows = list(db.scalars(select(RefreshToken).where(RefreshToken.session_id == sid)))
                assert len(rows) == 2 and sum(r.used_at is not None for r in rows) == 1
                assert all(
                    len(r.token_hash) == 64
                    and r.token_hash not in {refresh, client.cookies.get(REFRESH_COOKIE)}
                    for r in rows
                )
        finally:
            engine.dispose()
        bad = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "unexpected": password},
        )
        assert bad.status_code == 422 and password not in bad.text
        for secret in [
            password,
            access,
            refresh,
            csrf,
            str(client.cookies.get(REFRESH_COOKIE)),
            database_settings.session_signing_key.get_secret_value(),
            database_settings.database_url.get_secret_value(),
        ]:
            assert secret not in stream.getvalue()
            assert secret not in me.text and secret not in reply.text
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
        handler.close()


@REAL
async def test_csrf_origin_cookie_header_and_session_binding_are_all_required(
    client: httpx.AsyncClient,
) -> None:
    await new_identity(client)
    correct = client.headers["X-CSRF-Token"]
    for headers in [
        {"X-CSRF-Token": ""},
        {"X-CSRF-Token": "wrong-token"},
        {"Origin": "https://evil.invalid"},
        {"Origin": ""},
        {"Origin": "null"},
    ]:
        rejected = await client.post("/api/v1/auth/logout-all", headers=headers)
        assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "ERR_CSRF"
    # Matching attacker-chosen cookie/header must still fail the session's stored hash.
    client.cookies.set(CSRF_COOKIE, "attacker-chosen", domain="api.ledgerpilot.test", path="/")
    rejected = await client.post(
        "/api/v1/auth/logout-all", headers={"X-CSRF-Token": "attacker-chosen"}
    )
    assert rejected.status_code == 403
    client.cookies.set(CSRF_COOKIE, correct, domain="api.ledgerpilot.test", path="/")
    signed_out = await client.post("/api/v1/auth/logout-all")
    assert signed_out.status_code == 200
    assert all(client.cookies.get(k) is None for k in [ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE])
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@REAL
async def test_absolute_refresh_expiration_is_enforced_server_side(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    await new_identity(client)
    uid, sid = decode_access(database_settings, str(client.cookies.get(ACCESS_COOKIE)))
    engine = build_engine(database_settings)
    try:
        with Session(engine) as db, db.begin():
            set_context(db, uid)
            family = db.get(AuthSession, sid)
            assert family
            family.expires_at = now() - timedelta(seconds=1)
        reply = await client.post("/api/v1/auth/refresh")
        assert reply.status_code == 401
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    finally:
        engine.dispose()
