"""Regression cases discovered during the Phase 2/3 architecture audit."""

import json
import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_phase2_integration import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
    journal_request,
    mail_token,
    new_identity,
)
from test_phase3_integration import command, create_account, invoice, setup

from ledgerpilot.database import build_engine
from ledgerpilot.identity import repository, service
from ledgerpilot.identity.security import verify_password
from ledgerpilot.local_setup import FileSettings

# ruff: noqa: F811 -- pytest injects imported real-service fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


@pytest.mark.parametrize("kind", ["invoice", "bill"])
async def test_advance_cannot_overallocate_a_later_paid_invoice(
    client: httpx.AsyncClient, database_settings: FileSettings, kind: str
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        issued = await invoice(api, base, accounts, party, kind=kind)
        assert issued.status_code == 201, issued.text
        did = issued.json()["id"]
        deposit = await create_account(
            api, base, "deposits", "liability" if kind == "invoice" else "asset"
        )
        advance = await api.post(
            base + "/advances",
            json={
                **await command(api, base),
                "party_id": party,
                "direction": "customer" if kind == "invoice" else "vendor",
                "accounting_date": "2026-10-01",
                "deposit_account_id": deposit,
                "bank_account_id": accounts["bank"],
                "amount": "100",
            },
        )
        assert advance.status_code == 201, advance.text
        paid = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt" if kind == "invoice" else "payment",
                "party_id": party,
                "accounting_date": "2026-11-01",
                "bank_account_id": accounts["bank"],
                "control_account_id": accounts["ar" if kind == "invoice" else "ap"],
                "amount": "100",
                "allocations": [{"document_id": did, "amount": "100"}],
            },
        )
        assert paid.status_code == 201, paid.text
        before = await command(api, base)
        applied = await api.post(
            base + f"/advances/{advance.json()['id']}/applications",
            json={
                **before,
                "document_id": did,
                "accounting_date": "2026-10-02",
                "amount": "100",
            },
        )
        assert applied.status_code == 409, applied.text
        assert applied.json()["error"]["code"] == "ERR_ADVANCE"
        assert (await api.get(base)).json()["version"] == before["expected_version"]
        detail = await api.get(base + f"/trade-documents/{did}", params={"as_of": "2026-11-30"})
        assert Decimal(detail.json()["outstanding"]) == 0


async def test_reversal_cannot_precede_original(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, _ = await setup(api, bid)
        issued = await api.post(
            base + "/journals", json=journal_request(accounts["bank"], accounts["income"])
        )
        assert issued.status_code == 201, issued.text
        payload = {
            **await command(api, base),
            "accounting_date": "2026-09-30",
            "memo": "Reviewed correction",
        }
        rejected = await api.post(base + f"/journals/{issued.json()['id']}/reversal", json=payload)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "ERR_REVERSAL_DATE"
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        engine = build_engine(database_settings)
        try:
            with pytest.raises(DBAPIError) as failure, engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('app.user_id',:u,true), "
                        "set_config('app.business_id',:b,true)"
                    ),
                    {"u": uid, "b": bid},
                )
                connection.execute(
                    text(
                        "SELECT app.post_journal(:b,'2026-09-30','Invalid chronology',"
                        "'USD',:k,:h,1,CAST(:lines AS jsonb),:original)"
                    ),
                    {
                        "b": bid,
                        "k": uuid4().hex,
                        "h": "a" * 64,
                        "original": issued.json()["id"],
                        "lines": json.dumps(
                            [
                                {"account_id": accounts["bank"], "debit": "0", "credit": "100"},
                                {"account_id": accounts["income"], "debit": "100", "credit": "0"},
                            ]
                        ),
                    },
                )
            assert failure.value.orig.diag.message_primary == "ERR_REVERSAL_DATE"
        finally:
            engine.dispose()
        valid = await api.post(
            base + f"/journals/{issued.json()['id']}/reversal",
            json={
                **payload,
                "idempotency_key": uuid4().hex,
                "accounting_date": "2026-10-01",
            },
        )
        assert valid.status_code == 201, valid.text


async def test_future_close_tasks_do_not_block_earlier_readiness(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        future = await api.post(
            base + "/periods", json={"start_date": "2027-01-01", "end_date": "2027-12-31"}
        )
        assert future.status_code == 201, future.text
        task = await api.post(
            base + "/close-tasks",
            json={
                "period_id": future.json()["id"],
                "name": "Future close",
                "assigned_to": uid,
            },
        )
        assert task.status_code == 201, task.text
        earlier = await api.get(base + "/readiness", params={"as_of": "2026-12-31"})
        assert earlier.status_code == 200, earlier.text
        assert earlier.json()["mandatory_tasks_pending"] == 0
        later = await api.get(base + "/readiness", params={"as_of": "2027-12-31"})
        assert later.status_code == 200, later.text
        assert later.json()["mandatory_tasks_pending"] == 1


async def test_login_verified_before_reset_cannot_create_valid_session_after_reset(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    email, password, _ = await new_identity(client)
    requested = await client.post("/api/v1/auth/reset-request", json={"email": email})
    assert requested.status_code == 202, requested.text
    token = await mail_token(email)
    engine = build_engine(database_settings)
    try:
        # Real overlapping transactions at the exact login/reset race boundary.
        # No auth, hashing, SMTP or database implementation is substituted.
        with Session(engine, expire_on_commit=False) as pending:
            user = repository.by_email(pending, email)
            assert user is not None and verify_password(user.password_hash, password)
            reset = await client.post(
                "/api/v1/auth/reset",
                json={
                    "token": token,
                    "new_password": "A replacement private phrase " + uuid4().hex,
                },
            )
            assert reset.status_code == 200, reset.text
            from ledgerpilot.database import set_context

            set_context(pending, user.id)
            access, refresh, csrf = service.issue_session(pending, database_settings, user, False)
            pending.commit()
        client.cookies.clear()
        client.cookies.set(ACCESS_COOKIE, access)
        client.cookies.set(REFRESH_COOKIE, refresh)
        client.cookies.set("__Host-lp_csrf", csrf)
        client.headers["X-CSRF-Token"] = csrf
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    finally:
        engine.dispose()
