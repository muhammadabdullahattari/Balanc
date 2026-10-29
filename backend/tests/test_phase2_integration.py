"""Real PostgreSQL, real SMTP and ASGI HTTP; no substituted identity/tenant services."""

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pyotp
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from ledgerpilot.database import build_engine
from ledgerpilot.identity.dependencies import ACCESS_COOKIE, REFRESH_COOKIE
from ledgerpilot.identity.security import digest
from ledgerpilot.local_setup import (
    ROOT,
    FileSettings,
    connect,
    credential_url,
    read_values,
    validate_values,
)
from ledgerpilot.main import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires local PostgreSQL and Mailpit"
)
ORIGIN = "https://web.ledgerpilot.test"


@pytest.fixture(scope="module")
def database_settings() -> Iterator[FileSettings]:
    values = read_values(ROOT / ".env")
    validate_values(values)
    database_name = "ledgerpilot_test_" + uuid4().hex
    admin_url = make_url(credential_url(values, "POSTGRES_USER", "POSTGRES_PASSWORD"))
    migration_url = make_url(values["MIGRATION_DATABASE_URL"]).set(
        database=database_name, host="127.0.0.1"
    )
    runtime_url = make_url(values["DATABASE_URL"]).set(database=database_name, host="127.0.0.1")
    with connect(admin_url) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database_name), sql.Identifier(values["MIGRATION_DB_USER"])
            )
        )
    try:
        with connect(admin_url.set(database=database_name)) as admin:
            admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database_name)
                )
            )
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {},{}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(values["APP_DB_USER"]),
                    sql.Identifier(values["MIGRATION_DB_USER"]),
                )
            )
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            admin.execute(
                sql.SQL("CREATE SCHEMA app AUTHORIZATION {}").format(
                    sql.Identifier(values["MIGRATION_DB_USER"])
                )
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA app TO {}").format(
                    sql.Identifier(values["APP_DB_USER"])
                )
            )
            admin.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                ).format(sql.Identifier(values["MIGRATION_DB_USER"]))
            )
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend/migrations"))
        with pytest.MonkeyPatch.context() as env:
            env.setenv(
                "MIGRATION_DATABASE_URL", migration_url.render_as_string(hide_password=False)
            )
            env.setenv("MIGRATION_DB_USER", values["MIGRATION_DB_USER"])
            env.setenv("APP_DB_USER", values["APP_DB_USER"])
            command.upgrade(config, "head")
            command.upgrade(config, "head")  # Repeating upgrade must be harmless.
        yield FileSettings.model_validate(
            {
                **{
                    key.lower(): value
                    for key, value in values.items()
                    if key.lower() in FileSettings.model_fields
                },
                "app_env": "test",
                "database_url": runtime_url.render_as_string(hide_password=False),
                "auth_delivery_enabled": True,
                "frontend_origins": ORIGIN,
                "session_signing_key": secrets.token_urlsafe(48),
                "mfa_encryption_key": Fernet.generate_key().decode(),
                "smtp_host": "127.0.0.1",
                "smtp_port": 1025,
                "smtp_username": None,
                "smtp_password": None,
                "smtp_starttls": False,
            }
        )
    finally:
        # Only the unique disposable database created above may be removed.
        assert database_name.startswith("ledgerpilot_test_")
        assert database_name != values["POSTGRES_DB"]
        with connect(admin_url) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


@pytest.fixture
async def client(database_settings: FileSettings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, client=(f"127.0.0.{uuid4().int % 250 + 1}", 12345)
            ),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": ORIGIN},
        ) as browser,
    ):
        response = await browser.get("/api/v1/auth/csrf")
        browser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield browser


async def mail_token(email: str) -> str:
    async with httpx.AsyncClient() as mailbox:
        result = await mailbox.get(
            "http://127.0.0.1:8025/api/v1/search", params={"query": "to:" + email}
        )
        messages = result.json()["messages"]
        assert messages, "Expected a real SMTP delivery to the local mailbox"
        message = await mailbox.get("http://127.0.0.1:8025/api/v1/message/" + messages[0]["ID"])
        return str(message.json()["Text"]).replace("\r\n", "\n").split("\n\n")[1].strip()


async def new_identity(browser: httpx.AsyncClient) -> tuple[str, str, list[str]]:
    email = uuid4().hex + "@example.com"
    password = "Private phrase " + uuid4().hex
    response = await browser.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 202, response.text
    token = await mail_token(email)
    verified = await browser.post("/api/v1/auth/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    response = await browser.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    csrf = (await browser.get("/api/v1/auth/csrf")).json()["csrf_token"]
    browser.headers["X-CSRF-Token"] = csrf
    enrolled = await browser.post("/api/v1/auth/mfa/enroll")
    assert enrolled.status_code == 200, enrolled.text
    confirmed = await browser.post(
        "/api/v1/auth/mfa/confirm", json={"code": pyotp.TOTP(enrolled.json()["secret"]).now()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return email, password, confirmed.json()["recovery_codes"]


async def new_business(browser: httpx.AsyncClient) -> str:
    response = await browser.post(
        "/api/v1/businesses",
        json={
            "name": "Synthetic test business",
            "currency": "USD",
            "timezone": "America/New_York",
            "basis": "accrual",
            "jurisdiction": "US-test",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.asyncio
async def test_real_identity_lifecycle_and_secure_cookie_flags(client: httpx.AsyncClient) -> None:
    email, password, codes = await new_identity(client)
    assert (await client.get("/health/ready")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).json()["mfa"] is True
    for cookie in client.cookies.jar:
        assert cookie.secure
        assert cookie.expires is not None
        assert cookie.has_nonstandard_attr("HttpOnly")
        assert cookie.path == "/"
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_csrf_and_unknown_tenant_are_denied(client: httpx.AsyncClient) -> None:
    await new_identity(client)
    result = await client.post(
        "/api/v1/businesses", json={}, headers={"Origin": "https://evil.example"}
    )
    assert result.status_code == 403
    assert (await client.get("/api/v1/businesses/" + str(uuid4()))).status_code == 404


@pytest.mark.asyncio
async def test_owner_is_not_automatically_a_posting_accountant(client: httpx.AsyncClient) -> None:
    await new_identity(client)
    bid = await new_business(client)
    response = await client.post(
        f"/api/v1/businesses/{bid}/periods",
        json={"start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_database_rls_denies_forged_business_scope(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    await new_identity(client)
    first = await new_business(client)
    identity = (await client.get("/api/v1/auth/me")).json()["id"]
    engine = create_engine(database_settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with engine.begin() as db:
            db.execute(
                text(
                    "SELECT set_config('app.user_id',:u,true), "
                    "set_config('app.business_id',:b,true)"
                ),
                {"u": str(uuid4()), "b": first},
            )
            assert db.execute(text("SELECT id FROM app.businesses")).all() == []
            assert db.execute(text("SELECT id FROM app.accounts")).all() == []
        with engine.begin() as db:
            assert (
                db.execute(
                    text("SELECT nullif(current_setting('app.user_id',true),'')")
                ).scalar_one()
                is None
            )
            db.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": identity})
            assert db.execute(text("SELECT id FROM app.businesses")).scalar_one() == UUID(first)
    finally:
        engine.dispose()


@asynccontextmanager
async def accountant_context(
    owner: httpx.AsyncClient, settings: FileSettings
) -> AsyncIterator[tuple[str, httpx.AsyncClient]]:
    await new_identity(owner)
    bid = await new_business(owner)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, client=(f"127.0.0.{uuid4().int % 250 + 1}", 12345)
            ),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": ORIGIN},
        ) as accountant,
    ):
        accountant.headers["X-CSRF-Token"] = (await accountant.get("/api/v1/auth/csrf")).json()[
            "csrf_token"
        ]
        email, _, _ = await new_identity(accountant)
        invitation = await owner.post(
            f"/api/v1/businesses/{bid}/invitations", json={"email": email, "role": "accountant"}
        )
        assert invitation.status_code == 202, invitation.text
        accepted = await accountant.post(
            "/api/v1/invitations/accept", json={"token": await mail_token(email)}
        )
        assert accepted.status_code == 200, accepted.text
        yield bid, accountant


async def accounting_setup(accountant: httpx.AsyncClient, bid: str) -> tuple[str, str, str]:
    ids = []
    for code, name, kind in [
        ("1000", "Bank", "asset"),
        ("4000", "Sales", "income"),
        ("2000", "Loan", "liability"),
    ]:
        response = await accountant.post(
            f"/api/v1/businesses/{bid}/accounts", json={"code": code, "name": name, "kind": kind}
        )
        assert response.status_code == 201, response.text
        ids.append(str(response.json()["id"]))
    response = await accountant.post(
        f"/api/v1/businesses/{bid}/periods",
        json={"start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    assert response.status_code == 201, response.text
    return ids[0], ids[1], str(response.json()["id"])


def journal_request(bank: str, income: str, version: int = 0) -> dict[str, object]:
    return {
        "accounting_date": "2026-10-01",
        "memo": "Synthetic golden sale",
        "currency": "USD",
        "idempotency_key": uuid4().hex,
        "expected_version": version,
        "lines": [
            {"account_id": bank, "debit": "100.00"},
            {"account_id": income, "credit": "100.00"},
        ],
    }


@pytest.mark.asyncio
async def test_balanced_atomic_idempotent_posting_reversal_and_period_lock(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        bank, income, period = await accounting_setup(accountant, bid)
        request = journal_request(bank, income)
        posted = await accountant.post(f"/api/v1/businesses/{bid}/journals", json=request)
        assert posted.status_code == 201, posted.text
        jid = posted.json()["id"]
        repeated = await accountant.post(f"/api/v1/businesses/{bid}/journals", json=request)
        assert repeated.status_code == 201 and repeated.json()["id"] == jid
        conflict = await accountant.post(
            f"/api/v1/businesses/{bid}/journals", json={**request, "memo": "Different operation"}
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "ERR_IDEMPOTENCY_CONFLICT"
        stale = await accountant.post(
            f"/api/v1/businesses/{bid}/journals", json=journal_request(bank, income)
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "ERR_VERSION"
        balance = (
            await accountant.get(
                f"/api/v1/businesses/{bid}/trial-balance", params={"as_of": "2026-12-31"}
            )
        ).json()
        assert balance["balanced"] and balance["total_debit"] == "100.000000"
        assert balance["total_credit"] == "100.000000"
        version = (await accountant.get(f"/api/v1/businesses/{bid}")).json()["version"]
        reversed_journal = await accountant.post(
            f"/api/v1/businesses/{bid}/journals/{jid}/reversal",
            json={
                "accounting_date": "2026-10-02",
                "memo": "Reverse synthetic sale",
                "idempotency_key": uuid4().hex,
                "expected_version": version,
            },
        )
        assert reversed_journal.status_code == 201, reversed_journal.text
        original = (await accountant.get(f"/api/v1/businesses/{bid}/journals/{jid}")).json()
        assert original["status"] == "reversed"
        assert original["reversed_by"] == reversed_journal.json()["id"]
        final = (
            await accountant.get(
                f"/api/v1/businesses/{bid}/trial-balance", params={"as_of": "2026-12-31"}
            )
        ).json()
        assert all(row["balance"] == "0.000000" for row in final["items"])
        assert Decimal(final["total_debit"]) == 0 and Decimal(final["total_credit"]) == 0
        locked = await accountant.patch(
            f"/api/v1/businesses/{bid}/periods/{period}",
            json={"locked": True, "expected_version": 0},
        )
        assert locked.status_code == 200
        blocked = await accountant.post(
            f"/api/v1/businesses/{bid}/journals", json=journal_request(bank, income, 2)
        )
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "ERR_PERIOD_LOCKED"


@pytest.mark.asyncio
async def test_posting_guards_and_database_immutability(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        bank, income, _ = await accounting_setup(accountant, bid)
        invalid = journal_request(bank, income)
        invalid["lines"] = [
            {"account_id": bank, "debit": "100"},
            {"account_id": income, "credit": "99"},
        ]
        assert (
            await accountant.post(f"/api/v1/businesses/{bid}/journals", json=invalid)
        ).status_code == 400
        posted = await accountant.post(
            f"/api/v1/businesses/{bid}/journals", json=journal_request(bank, income)
        )
        assert posted.status_code == 201, posted.text
        uid = (await accountant.get("/api/v1/auth/me")).json()["id"]
        values = read_values(ROOT / ".env")
        migration_url = make_url(values["MIGRATION_DATABASE_URL"]).set(
            database=make_url(database_settings.database_url.get_secret_value()).database
        )
        engine = create_engine(migration_url, hide_parameters=True)
        try:
            for operation in [
                "UPDATE app.journals SET memo='changed' WHERE id=:j",
                "DELETE FROM app.journals WHERE id=:j",
                "UPDATE app.businesses SET currency='EUR' WHERE id=:b",
            ]:
                with pytest.raises(DBAPIError) as failure, engine.begin() as db:
                    db.execute(
                        text(
                            "SELECT set_config('app.user_id',:u,true),"
                            "set_config('app.business_id',:b,true)"
                        ),
                        {"u": uid, "b": bid},
                    )
                    db.execute(text(operation), {"j": posted.json()["id"], "b": bid})
                assert getattr(failure.value.orig, "sqlstate", None) == "23514"
        finally:
            engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_database_idempotency_has_one_financial_effect(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        bank, income, _ = await accounting_setup(accountant, bid)
        uid = (await accountant.get("/api/v1/auth/me")).json()["id"]
        import json

        engine = create_engine(
            database_settings.database_url.get_secret_value(), hide_parameters=True
        )
        operation_key = uuid4().hex

        def concurrent_post() -> str:
            with engine.begin() as db:
                db.execute(
                    text(
                        "SELECT set_config('app.user_id',:u,true),"
                        "set_config('app.business_id',:b,true)"
                    ),
                    {"u": uid, "b": bid},
                )
                return str(
                    db.execute(
                        text(
                            "SELECT app.post_journal(:b,'2026-10-01','concurrent',"
                            "'USD',:key,:h,0,CAST(:lines AS jsonb),NULL)"
                        ),
                        {
                            "b": bid,
                            "key": operation_key,
                            "h": "a" * 64,
                            "lines": json.dumps(
                                [
                                    {"account_id": bank, "debit": "100", "credit": "0"},
                                    {"account_id": income, "debit": "0", "credit": "100"},
                                ]
                            ),
                        },
                    ).scalar_one()
                )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: concurrent_post(), range(2)))
            assert results[0] == results[1]
            assert (await accountant.get(f"/api/v1/businesses/{bid}")).json()["version"] == 1
        finally:
            engine.dispose()


def operator_engine(settings: FileSettings):
    values = read_values(ROOT / ".env")
    url = make_url(values["MIGRATION_DATABASE_URL"]).set(
        host="127.0.0.1", database=make_url(settings.database_url.get_secret_value()).database
    )
    return create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})


def replace_cookie(browser: httpx.AsyncClient, name: str, value: str) -> None:
    for cookie in browser.cookies.jar:
        if cookie.name == name:
            cookie.value = value


@pytest.mark.asyncio
async def test_refresh_rotation_and_replay_revoke_the_whole_family(
    client: httpx.AsyncClient,
) -> None:
    await new_identity(client)
    old = client.cookies.get(REFRESH_COOKIE)
    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 200, response.text
    assert client.cookies.get(REFRESH_COOKIE) != old
    replace_cookie(client, REFRESH_COOKIE, str(old))
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401, replay.text
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_password_reset_is_one_use_and_revokes_old_sessions(
    client: httpx.AsyncClient,
) -> None:
    email, password, codes = await new_identity(client)
    old_access = client.cookies.get(ACCESS_COOKIE)
    requested = await client.post("/api/v1/auth/reset-request", json={"email": email})
    assert requested.status_code == 202
    token = await mail_token(email)
    replacement = "Replacement private phrase " + uuid4().hex
    reset = await client.post(
        "/api/v1/auth/reset", json={"token": token, "new_password": replacement}
    )
    assert reset.status_code == 200, reset.text
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    ).status_code == 401
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": replacement, "recovery_code": codes[0]},
    )
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    reused = await client.post(
        "/api/v1/auth/reset", json={"token": token, "new_password": replacement}
    )
    assert reused.status_code == 410, reused.text
    replace_cookie(client, ACCESS_COOKIE, str(old_access))
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_expired_verification_token_cannot_verify(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    email = uuid4().hex + "@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Synthetic private phrase " + uuid4().hex},
    )
    assert response.status_code == 202
    token = await mail_token(email)
    engine = operator_engine(database_settings)
    try:
        with engine.begin() as db:
            db.execute(text("SELECT set_config('app.login_email',:e,true)"), {"e": email})
            uid = db.execute(
                text("SELECT id FROM app.users WHERE email=:e"), {"e": email}
            ).scalar_one()
            db.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": str(uid)})
            db.execute(
                text(
                    "UPDATE app.action_tokens SET expires_at=now()-interval '1 minute' "
                    "WHERE token_hash=:h"
                ),
                {"h": digest(token)},
            )
        expired = await client.post("/api/v1/auth/verify", json={"token": token})
        assert expired.status_code == 410, expired.text
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_suspension_and_idle_timeout_apply_to_access_and_refresh(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    await new_identity(client)
    await new_business(client)
    uid = (await client.get("/api/v1/auth/me")).json()["id"]
    engine = operator_engine(database_settings)
    try:
        with engine.begin() as db:
            db.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": uid})
            db.execute(text("UPDATE app.sessions SET last_seen_at=now()-interval '16 minutes'"))
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await client.post("/api/v1/auth/refresh")).status_code == 401
        with engine.begin() as db:
            db.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": uid})
            db.execute(text("UPDATE app.sessions SET last_seen_at=now()"))
            db.execute(text("UPDATE app.users SET suspended=true"))
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_failed_logins_consume_distributed_rate_limits(client: httpx.AsyncClient) -> None:
    email = uuid4().hex + "@example.com"
    for _ in range(5):
        result = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "Nonexistent private phrase"}
        )
        assert result.status_code == 401, result.text
    result = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Nonexistent private phrase"}
    )
    assert result.status_code == 429, result.text


@pytest.mark.asyncio
async def test_request_bounds_and_errors_do_not_echo_secrets(client: httpx.AsyncClient) -> None:
    secret = "NEVER_ECHO_PRIVATE_INPUT_" + uuid4().hex
    invalid = await client.post(
        "/api/v1/auth/login", json={"email": "invalid", "password": secret, "unexpected": secret}
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    oversized = await client.post("/api/v1/auth/login", content=b"x" * 262145)
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_database_deferred_balance_and_currency_guards(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        bank, income, _ = await accounting_setup(accountant, bid)
        uid = (await accountant.get("/api/v1/auth/me")).json()["id"]
        engine = operator_engine(database_settings)
        try:
            with pytest.raises(DBAPIError) as failure, engine.begin() as db:
                db.execute(
                    text(
                        "SELECT set_config('app.user_id',:u,true),"
                        "set_config('app.business_id',:b,true)"
                    ),
                    {"u": uid, "b": bid},
                )
                db.execute(
                    text(
                        "INSERT INTO app.journals(id,business_id,accounting_date,memo,"
                        "currency,idempotency_key,request_hash,created_by) "
                        "VALUES(:id,:b,'2026-10-01','unbalanced','USD',:key,:h,:u)"
                    ),
                    {"id": str(uuid4()), "b": bid, "key": uuid4().hex, "h": "b" * 64, "u": uid},
                )
            assert getattr(failure.value.orig, "sqlstate", None) == "23514"
            request = journal_request(bank, income)
            request["currency"] = "EUR"
            assert (
                await accountant.post(f"/api/v1/businesses/{bid}/journals", json=request)
            ).status_code == 409
            assert (await accountant.get(f"/api/v1/businesses/{bid}")).json()["version"] == 0
        finally:
            engine.dispose()


@pytest.mark.asyncio
async def test_membership_revocation_takes_effect_on_existing_session(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        uid = (await accountant.get("/api/v1/auth/me")).json()["id"]
        engine = operator_engine(database_settings)
        try:
            with engine.begin() as db:
                db.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": uid})
                db.execute(
                    text("UPDATE app.memberships SET active=false WHERE business_id=:b"), {"b": bid}
                )
            assert (await accountant.get(f"/api/v1/businesses/{bid}/accounts")).status_code == 404
        finally:
            engine.dispose()


@pytest.mark.asyncio
async def test_policies_and_chronological_ledger_read_contracts(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    from datetime import UTC, datetime, timedelta

    async with accountant_context(client, database_settings) as (bid, accountant):
        bank, income, period = await accounting_setup(accountant, bid)
        accounts = await accountant.get(f"/api/v1/businesses/{bid}/accounts", params={"limit": 2})
        assert accounts.status_code == 200 and len(accounts.json()["items"]) == 2
        next_page = await accountant.get(
            f"/api/v1/businesses/{bid}/accounts",
            params={"limit": 2, "cursor": accounts.json()["next_cursor"]},
        )
        assert len(next_page.json()["items"]) == 1
        assert (await accountant.get(f"/api/v1/businesses/{bid}/periods")).json()["items"][0][
            "id"
        ] == period
        first = await accountant.post(
            f"/api/v1/businesses/{bid}/journals", json=journal_request(bank, income)
        )
        assert first.status_code == 201, first.text
        details = await accountant.get(f"/api/v1/businesses/{bid}/journals/{first.json()['id']}")
        assert details.status_code == 200 and len(details.json()["lines"]) == 2
        following = journal_request(bank, income, 1)
        following["accounting_date"] = "2026-10-02"
        assert (
            await accountant.post(f"/api/v1/businesses/{bid}/journals", json=following)
        ).status_code == 201
        cursor = None
        seen = []
        for _ in range(4):
            params = {"start": "2026-01-01", "end": "2026-12-31", "limit": 1}
            if cursor:
                params["cursor"] = cursor
            page = await accountant.get(f"/api/v1/businesses/{bid}/ledger", params=params)
            assert page.status_code == 200, page.text
            seen.extend(page.json()["items"])
            cursor = page.json()["next_cursor"]
        assert cursor is None and len({row["id"] for row in seen}) == 4
        assert [row["date"] for row in seen] == ["2026-10-01"] * 2 + ["2026-10-02"] * 2
        deactivated = await accountant.patch(
            f"/api/v1/businesses/{bid}/accounts/{bank}",
            json={"active": False, "expected_version": 0},
        )
        assert deactivated.status_code == 200 and deactivated.json()["version"] == 1
        stale = await accountant.patch(
            f"/api/v1/businesses/{bid}/accounts/{bank}",
            json={"active": True, "expected_version": 0},
        )
        assert stale.status_code == 409
        scheduled = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
        policy = await accountant.post(
            f"/api/v1/businesses/{bid}/policies",
            json={
                "basis": "cash",
                "jurisdiction": "US-test-v2",
                "effective_date": scheduled,
                "expected_version": 2,
            },
        )
        assert policy.status_code == 201, policy.text
        assert (await accountant.get(f"/api/v1/businesses/{bid}")).json()["basis"] == "accrual"
        assert (
            await accountant.get(
                f"/api/v1/businesses/{bid}/trial-balance", params={"as_of": scheduled}
            )
        ).json()["policy_version"] == 2
        assert (
            len((await accountant.get(f"/api/v1/businesses/{bid}/policies")).json()["items"]) == 2
        )
        spec = (await accountant.get("/openapi.json")).json()
        assert spec["components"]["securitySchemes"]["BrowserSession"]["in"] == "cookie"
        assert "ErrorEnvelope" in spec["components"]["schemas"]


@pytest.mark.asyncio
async def test_draft_versions_posting_transition_and_provenance(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        bank, income, _ = await accounting_setup(accountant, bid)
        body = {
            "accounting_date": "2026-10-01",
            "memo": "Draft transaction",
            "currency": "USD",
            "lines": [{"account_id": bank, "debit": "100"}],
        }
        draft = await client.post(f"/api/v1/businesses/{bid}/journal-drafts", json=body)
        assert draft.status_code == 201, draft.text
        jid = draft.json()["id"]
        before = (
            await accountant.get(
                f"/api/v1/businesses/{bid}/trial-balance", params={"as_of": "2026-12-31"}
            )
        ).json()
        assert Decimal(before["total_debit"]) == 0
        invalid = await accountant.post(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}/post",
            json={
                "expected_version": 0,
                "expected_business_version": 0,
                "idempotency_key": uuid4().hex,
            },
        )
        assert invalid.status_code == 400, invalid.text
        body["lines"].append({"account_id": income, "credit": "100"})
        changed = await client.patch(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}", json={**body, "expected_version": 0}
        )
        assert changed.status_code == 200 and changed.json()["version"] == 1, changed.text
        stale = await client.patch(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}", json={**body, "expected_version": 0}
        )
        assert stale.status_code == 409
        publish = {
            "expected_version": 1,
            "expected_business_version": 0,
            "idempotency_key": uuid4().hex,
        }
        denied = await client.post(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}/post", json=publish
        )
        assert denied.status_code == 403
        posted = await accountant.post(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}/post", json=publish
        )
        assert posted.status_code == 201 and posted.json()["id"] == jid, posted.text
        repeated = await accountant.post(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}/post", json=publish
        )
        assert repeated.status_code == 201 and repeated.json()["id"] == jid, repeated.text
        info = (await accountant.get(f"/api/v1/businesses/{bid}/journals/{jid}")).json()
        assert info["status"] == "posted" and info["version"] == 2
        assert info["period_id"] and info["policy_id"] and info["posted_at"]
        assert info["source_id"] == jid and info["source_kind"] == "manual_journal"
        assert info["approved_by"] == (await accountant.get("/api/v1/auth/me")).json()["id"]
        locked = await client.patch(
            f"/api/v1/businesses/{bid}/journal-drafts/{jid}", json={**body, "expected_version": 2}
        )
        assert locked.status_code == 409


def test_runtime_pool_refuses_the_schema_owner(database_settings: FileSettings) -> None:
    operator = operator_engine(database_settings)
    # Fault injection after settings validation; actual database grants remain real.
    unsafe = database_settings.model_copy(
        update={
            "app_db_user": operator.url.username,
            "database_url": SecretStr(operator.url.render_as_string(hide_password=False)),
        }
    )
    engine = build_engine(unsafe)
    try:
        with pytest.raises(SQLAlchemyError), engine.connect():
            pytest.fail("The schema owner must not obtain an API runtime connection")
    finally:
        engine.dispose()
        operator.dispose()
