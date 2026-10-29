"""Real cutover imports reconcile without fabricated sales or bank movements."""

import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401 -- actual PostgreSQL/API fixture
    database_settings,  # noqa: F401 -- isolated migrated database
)
from test_phase3_integration import command, create_account, setup

from ledgerpilot.local_setup import FileSettings

# ruff: noqa: F811 -- pytest injects imported fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and SMTP"
)


async def test_reviewed_opening_import_reconciles_and_settles_without_historic_cash(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        equity = await create_account(api, base, "opening_equity", "equity")
        loan_asset = await create_account(api, base, "loan_asset", "asset", "loan_receivable")
        content = "kind,party_id,reference,original_date,due_date,amount,control_account_code\n"
        for kind, ref, amount, code in [
            ("customer", "OLD-AR", "100", "ar"),
            ("supplier", "OLD-AP", "60", "ap"),
            ("loan_payable", "OLD-LP", "200", "loan"),
            ("loan_receivable", "OLD-LR", "40", "loan_asset"),
        ]:
            content += f"{kind},{party},{ref},2025-12-01,2026-01-15,{amount},{code}\n"
        request = {
            "mode": "opening",
            "content": content,
            "cutover_date": "2026-10-01",
            "opening_equity_account_code": "opening_equity",
            "review_reason": "Reconciled to accountant-approved legacy closing balances",
            "expected_trial": {
                "ar": "100",
                "ap": "-60",
                "loan": "-200",
                "loan_asset": "40",
                "opening_equity": "120",
            },
        }
        preview = await api.post(base + "/imports/preview", json=request)
        assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
        apply = {
            **request,
            **await command(api, base),
            "preview_hash": preview.json()["preview_hash"],
        }
        denied = await client.post(base + "/imports/apply", json=apply)
        assert denied.status_code == 403, denied.text
        wrong = {**request, "expected_trial": {**request["expected_trial"], "ar": "101"}}
        bad_preview = await api.post(base + "/imports/preview", json=wrong)
        rolled_back = await api.post(
            base + "/imports/apply",
            json={
                **wrong,
                **await command(api, base),
                "preview_hash": bad_preview.json()["preview_hash"],
            },
        )
        assert rolled_back.status_code == 409 and "ERR_IMPORT_TOTAL" in rolled_back.text
        assert (await api.get(base + "/opening-balances")).json()["items"] == []
        result = await api.post(base + "/imports/apply", json=apply)
        assert result.status_code == 201, result.text
        repeat = await api.post(base + "/imports/apply", json=apply)
        assert repeat.status_code == 201 and repeat.json() == result.json(), repeat.text
        duplicate = await api.post(
            base + "/imports/apply", json={**apply, **await command(api, base)}
        )
        assert duplicate.status_code == 409 and "ERR_FILE_ALREADY_IMPORTED" in duplicate.text
        rows = (await api.get(base + "/opening-balances")).json()["items"]
        assert len(rows) == 4
        assert all(r["due_date"] == "2026-01-15" and r["equity_account_id"] == equity for r in rows)
        docs = (await api.get(base + "/trade-documents", params={"as_of": "2026-10-01"})).json()[
            "items"
        ]
        assert len(docs) == 2 and all(d["opening_source_date"] == "2025-12-01" for d in docs)
        before = await api.get(base + "/trial-balance", params={"as_of": "2026-09-30"})
        assert Decimal(before.json()["total_debit"]) == 0
        pnl = await api.get(
            base + "/reports/profit-loss",
            params={"start": "2026-10-01", "end": "2026-10-31", "basis": "accrual"},
        )
        assert pnl.status_code == 200 and Decimal(pnl.json()["net_profit"]) == 0, pnl.text
        cash = await api.get(
            base + "/reports/profit-loss",
            params={"start": "2026-10-01", "end": "2026-10-31", "basis": "cash"},
        )
        assert cash.status_code == 409 and "ERR_CASH_POLICY" in cash.text
        for doc in docs:
            customer = doc["kind"] == "invoice"
            paid = await api.post(
                base + "/payments",
                json={
                    **await command(api, base),
                    "kind": "receipt" if customer else "payment",
                    "party_id": party,
                    "accounting_date": "2026-10-02",
                    "bank_account_id": a["bank"],
                    "control_account_id": a["ar" if customer else "ap"],
                    "amount": "40" if customer else "60",
                    "allocations": [
                        {"document_id": doc["id"], "amount": "40" if customer else "60"}
                    ],
                },
            )
            assert paid.status_code == 201, paid.text
        loans = (await api.get(base + "/loans", params={"as_of": "2026-10-01"})).json()["items"]
        for loan in loans:
            paid = await api.post(
                base + f"/loans/{loan['id']}/movements",
                json={
                    **await command(api, base),
                    "kind": "repayment",
                    "accounting_date": "2026-10-02",
                    "bank_account_id": a["bank"],
                    "principal": "200" if loan["direction"] == "payable" else "40",
                },
            )
            assert paid.status_code == 201, paid.text
        controls = await api.get(base + "/reports/controls", params={"as_of": "2026-10-31"})
        assert controls.json()["balanced"], controls.text
        statement = await api.get(
            base + f"/parties/{party}/statement", params={"as_of": "2026-10-31", "role": "customer"}
        )
        assert Decimal(statement.json()["total"]) == 60
        assert loan_asset


async def test_opening_preview_rejects_missing_review_invalid_dates_and_duplicate_sources(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, _, party = await setup(api, bid)
        await create_account(api, base, "opening_equity", "equity")
        header = "kind,party_id,reference,original_date,due_date,amount,control_account_code\n"
        line = f"customer,{party},OLD,2025-01-01,2025-02-01,100,ar\n"
        request = {
            "mode": "opening",
            "cutover_date": "2026-10-01",
            "content": header + line,
            "expected_trial": {"ar": "100", "opening_equity": "-100"},
        }
        preview = await api.post(base + "/imports/preview", json=request)
        assert not preview.json()["can_apply"]
        for content in [
            header + line + line,
            header + line.replace("2025-01-01", "2027-01-01"),
            header + line.replace(",ar\n", ",opening_equity\n"),
        ]:
            preview = await api.post(
                base + "/imports/preview",
                json={
                    **request,
                    "content": content,
                    "opening_equity_account_code": "opening_equity",
                    "review_reason": "Reviewed source " + uuid4().hex,
                },
            )
            assert preview.status_code == 200 and not preview.json()["can_apply"], preview.text
