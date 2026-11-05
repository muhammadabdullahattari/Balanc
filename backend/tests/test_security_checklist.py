"""Checklist regression gates: transport, routes, tenant isolation, rates and audit."""

import os
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import setup

from ledgerpilot.audit.models import AuditEvent
from ledgerpilot.config import Settings
from ledgerpilot.database import set_context
from ledgerpilot.identity.dependencies import ACCESS_COOKIE
from ledgerpilot.identity.security import decode_access, digest
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# ruff: noqa: F811 -- isolated real-service fixtures
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires local PostgreSQL/Mailpit"
)


def hosted() -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+psycopg://ledgerpilot_app:synthetic@localhost/ledgerpilot?sslmode=verify-full",
        session_signing_key="synthetic-checklist-key-at-least-32-bytes",
        frontend_origins="https://web.ledgerpilot.test",
    )


def protected_headers(response: httpx.Response) -> None:
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "camera=()" in response.headers["Permissions-Policy"]


async def test_hosted_headers_cover_success_cors_rejections_errors_and_hidden_docs() -> None:
    app = create_app(hosted())

    @app.get("/_test/unexpected")
    def unexpected():
        raise ValueError("PRIVATE-CHECKLIST-DETAIL")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://api.test",
    ) as api:
        for path in ["/health/live", "/docs", "/redoc", "/openapi.json", "/_test/unexpected"]:
            reply = await api.get(path)
            protected_headers(reply)
            assert reply.headers["Strict-Transport-Security"] == "max-age=31536000"
            assert reply.status_code == (
                200 if path == "/health/live" else 500 if path.endswith("unexpected") else 404
            )
            assert "PRIVATE-CHECKLIST-DETAIL" not in reply.text
        assert not app.debug
        reply = await api.options(
            "/api/v1/auth/login",
            headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"},
        )
        assert reply.status_code == 400 and "Access-Control-Allow-Origin" not in reply.headers
        protected_headers(reply)
        reply = await api.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://web.ledgerpilot.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-csrf-token",
            },
        )
        assert (
            reply.status_code == 200
            and reply.headers["Access-Control-Allow-Origin"] == "https://web.ledgerpilot.test"
        )
        protected_headers(reply)
        reply = await api.post("/api/v1/auth/register", json={})
        assert reply.status_code == 403
        protected_headers(reply)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as api:
        reply = await api.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://web.ledgerpilot.test",
                "Access-Control-Request-Method": "POST",
                "X-Forwarded-Proto": "https",
            },
        )
        assert reply.status_code == 403 and reply.json()["error"]["code"] == "ERR_HTTPS_REQUIRED"
        protected_headers(reply)
        assert "Strict-Transport-Security" not in reply.headers


def test_every_private_api_operation_declares_owned_authentication() -> None:
    spec = create_app(hosted()).openapi()
    public = {
        ("/health/live", "get"),
        ("/health/ready", "get"),
        ("/api/v1/auth/csrf", "get"),
        ("/api/v1/auth/google/config", "get"),
        *(
            ("/api/v1/auth/" + name, "post")
            for name in [
                "register",
                "verify",
                "verification-request",
                "reset-request",
                "reset",
                "login",
                "google/challenge",
                "google/login",
                "refresh",
            ]
        ),
    }
    seen = set()
    private = 0
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "patch", "delete", "put"}:
                continue
            if (path, method) in public:
                seen.add((path, method))
            else:
                assert operation.get("security") == [{"BrowserSession": []}], (path, method)
                private += 1
    assert seen == public and private > 100
    # No generic prompt/tool executor or inbound payment webhook exists in P1.
    assert not any("webhook" in p or "/execute" in p for p in spec["paths"])


@REAL
async def test_authenticated_non_tenant_routes_have_durable_user_rate_limits(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (_, api):
        app = api._transport.app
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        with app.state.rate_sessions.begin() as db:
            db.execute(
                text(
                    "UPDATE app.auth_rate_buckets SET started_at=now(),attempts=240,"
                    "blocked_until=NULL WHERE key=:key"
                ),
                {"key": digest(f"user:{uid}")},
            )
        for path in ["/api/v1/auth/me", "/api/v1/businesses"]:
            reply = await api.get(path)
            assert reply.status_code == 429 and reply.json()["error"]["code"] == "ERR_RATE_LIMIT", (
                reply.text
            )
            assert 1 <= int(reply.headers["Retry-After"]) <= 120
        with app.state.rate_sessions.begin() as db:
            db.execute(
                text(
                    "UPDATE app.auth_rate_buckets SET started_at=now()-interval '2 minutes',"
                    "attempts=0,blocked_until=NULL WHERE key=:key"
                ),
                {"key": digest(f"user:{uid}")},
            )
        assert (await api.get("/api/v1/auth/me")).status_code == 200


@REAL
async def test_tenant_rls_and_immutable_admin_audit_are_enforced_in_postgres(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        app = api._transport.app
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        with app.state.sessions.begin() as db:
            set_context(db, uid, UUID(bid))
            rows = db.execute(
                text("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='app' AND c.relkind IN ('r','p')
              AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
                AND a.attname='business_id' AND NOT a.attisdropped)""")
            ).all()
            assert len(rows) > 20 and all(enabled and forced for _, enabled, forced in rows)
            assert not db.scalar(
                text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user")
            )
            events = list(db.scalars(select(AuditEvent).where(AuditEvent.business_id == UUID(bid))))
            assert any(e.action == "account.created" and e.user_id == uid for e in events)
            event_id = events[0].id
        with pytest.raises(DBAPIError), app.state.sessions.begin() as db:
            set_context(db, uid, UUID(bid))
            db.execute(text("DELETE FROM app.audit_events WHERE id=:id"), {"id": event_id})
        # Actual routes enforce ownership even if a caller ignores all frontend guards.
        for suffix in ["", "/accounts", "/audit-events", "/evidence/" + str(uuid4()) + "/original"]:
            reply = await api.get(f"/api/v1/businesses/{uuid4()}" + suffix)
            assert reply.status_code == 404, reply.text
        assert (await api.get(base)).status_code == 200


async def test_ocr_rejects_non_ascii_bearer_headers_without_parser_or_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledgerpilot.documents.ocr_service import app

    monkeypatch.setenv("OCR_SERVICE_TOKEN", "synthetic-private-ocr-token-at-least-32-bytes")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ocr.test"
    ) as api:
        reply = await api.post(
            "/extract", content=b"untrusted", headers=[(b"authorization", b"Bearer \xff")]
        )
    assert reply.status_code == 401 and reply.json() == {"error": "unauthorized"}
