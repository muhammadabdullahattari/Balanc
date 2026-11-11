"""Verify prepaid schedule and independent recognition on local PostgreSQL."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import (
    ORIGIN,
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
    mail_token,
    new_identity,
)
from test_phase3_integration import command, setup

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.workspace import prepaids

# ruff: noqa: F811 -- shared service-backed fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_prepaid_rounding_approval_close_guard_and_duplicate_posting(
    client: httpx.AsyncClient, database_settings: FileSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prepaids, "now", lambda: datetime(2026, 12, 31, 20, tzinfo=UTC))
    async with accountant_context(client, database_settings) as (bid, accountant):
        base, accounts, _ = await setup(accountant, bid)
        asset = await accountant.post(
            base + "/accounts",
            json={"code": "prepaid", "name": "Prepaid insurance", "kind": "asset"},
        )
        assert asset.status_code == 201, asset.text
        asset_id = asset.json()["id"]
        journal = await accountant.post(
            base + "/journals",
            json={
                **await command(accountant, base),
                "accounting_date": "2026-10-01",
                "currency": "USD",
                "memo": "Insurance prepayment",
                "lines": [
                    {"account_id": asset_id, "debit": "100"},
                    {"account_id": accounts["bank"], "credit": "100"},
                ],
            },
        )
        assert journal.status_code == 201, journal.text
        assert (await accountant.get(base + "/prepaids")).status_code == 404
        settings = database_settings.model_copy(
            update={"p2_prepaid_business_ids": frozenset({UUID(bid)})}
        )
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as prepare,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                headers={"Origin": ORIGIN},
            ) as approve,
        ):
            approve.headers["X-CSRF-Token"] = (await approve.get("/api/v1/auth/csrf")).json()[
                "csrf_token"
            ]
            approver_email, _, _ = await new_identity(approve)
            invitation = await client.post(
                base + "/invitations", json={"email": approver_email, "role": "accountant"}
            )
            assert invitation.status_code == 202, invitation.text
            accepted = await approve.post(
                "/api/v1/invitations/accept", json={"token": await mail_token(approver_email)}
            )
            assert accepted.status_code == 200, accepted.text
            source = (await prepare.get(base + "/prepaids/sources")).json()["items"][0]
            data = {
                "source_line_id": source["id"],
                "expense_account_id": accounts["cost"],
                "description": "Insurance",
                "first_month": "2026-10-01",
                "months": 3,
            }
            saved = await prepare.post(base + "/prepaids", json=data)
            assert saved.status_code == 201, saved.text
            assert (await prepare.post(base + "/prepaids", json=data)).status_code == 409
            assert (await approve.get(f"/api/v1/businesses/{uuid4()}/prepaids")).status_code == 404
            sid = saved.json()["id"]
            schedule = (await approve.get(base + "/prepaids")).json()["items"][0]
            assert [Decimal(row["amount"]) for row in schedule["installments"]] == [
                Decimal("33.34"),
                Decimal("33.33"),
                Decimal("33.33"),
            ]
            state = await approve.get(base + "/readiness", params={"as_of": "2026-12-31"})
            assert state.status_code == 200, state.text
            assert state.json()["prepaid_installments_pending"] == 3
            period = (await approve.get(base + "/periods")).json()["items"][0]
            lock = await approve.patch(
                base + f"/periods/{period['id']}",
                json={"expected_version": period["version"], "locked": True},
            )
            assert lock.status_code == 409 and "ERR_PREPAID_PENDING" in lock.text
            route = base + f"/prepaids/{sid}/recognize"
            first = {**await command(prepare, base), "due_date": "2026-10-31"}
            assert (await prepare.post(route, json=first)).status_code == 403
            out_of_order = await approve.post(
                route, json={**await command(approve, base), "due_date": "2026-11-30"}
            )
            assert out_of_order.status_code == 409, out_of_order.text
            posted = await approve.post(route, json=first)
            assert posted.status_code == 200, posted.text
            assert (await approve.post(route, json=first)).json() == posted.json()
            for due in ("2026-11-30", "2026-12-31"):
                response = await approve.post(
                    route, json={**await command(approve, base), "due_date": due}
                )
                assert response.status_code == 200, response.text
            tb = await approve.get(base + "/trial-balance", params={"as_of": "2026-12-31"})
            assert tb.status_code == 200, tb.text
            amounts = {
                row["account_id"]: Decimal(row["debit"]) - Decimal(row["credit"])
                for row in tb.json()["items"]
            }
            assert amounts.get(asset_id, 0) == 0
            assert amounts[accounts["cost"]] == 100
            reversal = await approve.post(
                base + f"/journals/{journal.json()['id']}/reversal",
                json={
                    **await command(approve, base),
                    "accounting_date": "2026-12-31",
                    "memo": "Reverse",
                },
            )
            assert reversal.status_code == 409 and "ERR_PREPAID_REVERSAL" in reversal.text
