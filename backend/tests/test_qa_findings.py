"""QA regressions using isolated PostgreSQL, real SMTP and scanner services."""

import json
import logging
import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import event, select, text
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
    mail_token,
)
from test_phase3_integration import command, invoice, setup

from ledgerpilot.database import set_context
from ledgerpilot.documents.storage import scan
from ledgerpilot.identity import repository, service
from ledgerpilot.identity.dependencies import ACCESS_COOKIE, Principal
from ledgerpilot.identity.models import ActionToken, AuthSession, MFAFactor, User
from ledgerpilot.identity.schemas import Credentials, Registration
from ledgerpilot.identity.security import decode_access, digest, now
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.middleware import rate_limited
from ledgerpilot.shared.query_limits import bounded_scalars
from ledgerpilot.shared.telemetry import emit, request_logging
from ledgerpilot.tenancy import service as tenancy
from ledgerpilot.trade import repository as trade

# ruff: noqa: F811 -- shared disposable database fixtures
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires local PostgreSQL/Mailpit"
)


def test_diagnostic_logs_preserve_context_without_exception_messages(caplog, monkeypatch):
    logger = logging.getLogger("ledgerpilot.operations")
    logger.disabled = False
    monkeypatch.setattr(logging.getLogger("ledgerpilot"), "propagate", True)
    with (
        caplog.at_level(logging.INFO, logger=logger.name),
        request_logging({"request_id": str(uuid4()), "business_id": str(uuid4())}),
    ):
        try:
            raise RuntimeError("private-password sql-parameter token-value")
        except RuntimeError as error:
            emit("worker.failed", error=error, code="ERR_WORKER", password="must-not-log")
    value = json.loads(caplog.records[-1].message)
    assert value["error_type"] == "RuntimeError"
    assert value["request_id"] and value["business_id"] and value["frames"]
    assert (
        value["frames"][-1]["function"]
        == "test_diagnostic_logs_preserve_context_without_exception_messages"
    )
    assert not any(
        secret in caplog.text
        for secret in ["private-password", "sql-parameter", "token-value", "must-not-log"]
    )


@REAL
async def test_smtp_outage_keeps_pending_signup_and_rotates_codes_safely(
    client: httpx.AsyncClient, database_settings: FileSettings
):
    app = client._transport.app
    offline = database_settings.model_copy(update={"smtp_port": 1})
    email = uuid4().hex + "@example.com"
    registration = Registration(email=email, password="Private phrase 7! " + uuid4().hex)
    csrf = uuid4().hex
    with app.state.sessions.begin() as db:
        service.register(db, offline, registration, csrf)
    with app.state.sessions.begin() as db:
        user = repository.by_email(db, email)
        assert user and not user.verified and user.verification_attempts == 0
        uid = user.id
        set_context(db, uid)
        assert db.scalar(select(ActionToken).where(ActionToken.user_id == uid)) is None
        with pytest.raises(DomainError) as denied:
            service.login(
                db, database_settings, Credentials.model_validate(registration.model_dump())
            )
        assert denied.value.code == "ERR_EMAIL_UNVERIFIED"
        with pytest.raises(DomainError) as denied:
            service.login(db, database_settings, Credentials(email=email, password="incorrect"))
        assert denied.value.code == "ERR_AUTH"  # Never reveal account state without its password.
        service.request_action(db, database_settings, email, "verify", csrf)
    first = await mail_token(email)
    with app.state.sessions.begin() as db:
        set_context(db, uid)
        service.request_action(db, offline, email, "verify", csrf)
    with app.state.sessions.begin() as db:
        set_context(db, uid)
        user = db.get(User, uid)
        assert user.verification_attempts == 1
        token = db.get(ActionToken, digest(first))
        assert token and token.used_at is None  # A failed resend leaves the delivered code usable.
        service.register(db, offline, registration, csrf)
    with app.state.sessions.begin() as db:
        set_context(db, uid)
        assert db.get(ActionToken, digest(first)).used_at is not None
        assert db.get(User, uid).verification_attempts == 1
        service.request_action(db, database_settings, email, "verify", csrf)
    latest = await mail_token(email)
    assert latest != first
    with app.state.sessions.begin() as db:
        service.verify(db, latest, csrf)
    with app.state.sessions.begin() as db:
        set_context(db, uid)
        assert db.get(User, uid).verified


@REAL
async def test_mfa_enrollment_refreshes_preloaded_user_and_verified_factor(
    client: httpx.AsyncClient, database_settings: FileSettings
):
    async with accountant_context(client, database_settings) as (bid, api):
        uid, sid = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        sessions = api._transport.app.state.sessions
        with sessions.begin() as stale:
            set_context(stale, uid)
            user = stale.get(User, uid)
            factor = stale.get(MFAFactor, uid)
            assert user and factor and factor.verified
            # Simulate an identity-map object loaded before another transaction's change.
            from sqlalchemy.orm.attributes import set_committed_value

            set_committed_value(factor, "verified", False)
            with pytest.raises(DomainError) as error:
                service.enroll_mfa(stale, database_settings, uid)
            assert error.value.code == "ERR_MFA_ENROLLED"
            assert factor.verified  # Locked query refreshed the stale factor.
        with sessions.begin() as db:
            set_context(db, uid)
            who = Principal(db.get(User, uid), db.get(AuthSession, sid))
            with pytest.raises(DomainError) as error:
                tenancy.accept(db, who, uuid4().hex)
            assert error.value.code == "ERR_LINK_EXPIRED" and error.value.status == 410
            db.rollback()  # Invalid invite deliberately aborts its transaction in PostgreSQL.


@REAL
async def test_outstanding_snapshots_share_query_and_keep_historical_dates(
    client: httpx.AsyncClient, database_settings: FileSettings, monkeypatch
):
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        source = await invoice(api, base, accounts, party)
        assert source.status_code == 201, source.text
        did = UUID(source.json()["id"])
        paid = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-11-01",
                "bank_account_id": accounts["bank"],
                "control_account_id": accounts["ar"],
                "amount": "40",
                "allocations": [{"document_id": str(did), "amount": "40"}],
            },
        )
        assert paid.status_code == 201, paid.text
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        app = api._transport.app
        queries = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        with app.state.sessions.begin() as db:
            set_context(db, uid, UUID(bid))
            event.listen(app.state.engine, "before_cursor_execute", observe)
            try:
                values = trade.due_snapshots(db, UUID(bid), [date(2026, 10, 31), date.max])
            finally:
                event.remove(app.state.engine, "before_cursor_execute", observe)
            assert len(queries) == 1
            assert values[date(2026, 10, 31)][did] == 100
            assert values[date.max][did] == 60
            # Both SQL helper variants must reject overflow rather than return partial totals.
            monkeypatch.setattr("ledgerpilot.shared.query_limits.REPORT_LIMIT", 0)
            with pytest.raises(DomainError) as error:
                bounded_scalars(db, select(User).where(User.id == uid))
            assert error.value.code == "ERR_QUOTA"


@REAL
async def test_configured_rate_window_and_existing_indexes(client: httpx.AsyncClient):
    app = client._transport.app
    key = "qa-window-" + uuid4().hex
    assert rate_limited(app.state.rate_sessions, [(key, 1)], window_seconds=120) == 0
    with app.state.rate_sessions.begin() as db:
        db.execute(
            text(
                "UPDATE app.auth_rate_buckets SET started_at=now()-interval '90 seconds' "
                "WHERE key=:key"
            ),
            {"key": key},
        )
    wait = rate_limited(app.state.rate_sessions, [(key, 1)], window_seconds=120)
    assert 1 <= wait <= 31
    # A still-active cooldown survives a window rollover or shorter configuration.
    with app.state.rate_sessions.begin() as db:
        db.execute(
            text(
                "UPDATE app.auth_rate_buckets SET blocked_until=now()+interval '90 seconds' "
                "WHERE key=:key"
            ),
            {"key": key},
        )
    assert rate_limited(app.state.rate_sessions, [(key, 1)], window_seconds=60) >= 89
    with app.state.rate_sessions.begin() as db:
        db.execute(
            text(
                "UPDATE app.auth_rate_buckets SET started_at=now()-interval '90 seconds', "
                "blocked_until=now()-interval '1 second' WHERE key=:key"
            ),
            {"key": key},
        )
    assert rate_limited(app.state.rate_sessions, [(key, 1)], window_seconds=60) == 0
    with app.state.sessions.begin() as db:
        indexes = list(
            db.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='app' "
                    "AND tablename IN ('operations','bank_rows')"
                )
            ).scalars()
        )
        assert any("business_id, state" in definition for definition in indexes)
        assert any("business_id, key" in definition for definition in indexes)


@REAL
async def test_maximum_bank_import_is_one_insert_and_reimport_is_idempotent(
    client: httpx.AsyncClient, database_settings: FileSettings
):
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, _ = await setup(api, bid)
        payload = {
            "account_id": accounts["bank"],
            "content": "date,description,amount,id\n"
            + "".join(f"2026-10-01,QA source,10,qa-{i}\n" for i in range(500)),
            "mapping": {
                "date_column": "date",
                "description_column": "description",
                "amount_column": "amount",
                "external_id_column": "id",
            },
        }
        preview = await api.post(base + "/bank-imports/preview", json=payload)
        assert preview.status_code == 200, preview.text
        payload["expected_hash"] = preview.json()["file_hash"]
        inserts = []
        engine = api._transport.app.state.engine

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO app.bank_rows"):
                inserts.append(statement)

        event.listen(engine, "before_cursor_execute", observe)
        try:
            reply = await api.post(base + "/bank-imports", json=payload)
            assert reply.status_code == 201 and reply.json()["created"] == 500, reply.text
            replay = await api.post(base + "/bank-imports", json=payload)
            assert replay.status_code == 201 and replay.json()["created"] == 0, replay.text
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert len(inserts) == 1


@pytest.mark.skipif(
    os.environ.get("RUN_DOCUMENT_SCANNER_TESTS") != "1", reason="Requires local ClamAV"
)
def test_scanner_session_clean_and_eicar(database_settings: FileSettings):
    assert scan(b"clean QA document", database_settings).startswith("ClamAV ")
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    with pytest.raises(DomainError) as error:
        scan(eicar, database_settings)
    assert error.value.code == "ERR_MALWARE"


@REAL
async def test_valid_session_error_codes_are_actionable(
    client: httpx.AsyncClient, database_settings: FileSettings
):
    async with accountant_context(client, database_settings) as (_, api):
        uid, sid = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        sessions = api._transport.app.state.sessions
        with sessions.begin() as db:
            set_context(db, uid)
            family = db.get(AuthSession, sid)
            family.last_seen_at = now() - timedelta(minutes=16)
        response = await api.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "ERR_SESSION_IDLE"
