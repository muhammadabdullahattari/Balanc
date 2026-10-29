"""Corrections remain sourced; cash income requires explicit supported evidence."""

import os
from decimal import Decimal

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_closure import clean_upload, cleanup_originals  # noqa: F401
from test_phase3_integration import command, create_account, setup

from ledgerpilot.local_setup import FileSettings

# ruff: noqa: F811 -- real fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires real PostgreSQL/SMTP/ClamAV"
)


async def test_opening_corrections_and_reviewed_cash_recognition_reconcile(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        await create_account(api, base, "opening_equity", "equity")
        data = {
            "mode": "opening",
            "cutover_date": "2026-10-01",
            "opening_equity_account_code": "opening_equity",
            "review_reason": "Legacy totals approved",
            "expected_trial": {"ar": "100", "loan": "-100"},
            "content": (
                "kind,party_id,reference,original_date,due_date,amount,control_account_code\n"
            )
            + f"customer,{party},OLD-AR,2025-01-01,2026-01-01,100,ar\n"
            f"loan_payable,{party},OLD-LOAN,2025-01-01,2026-01-01,100,loan\n",
        }
        preview = await api.post(base + "/imports/preview", json=data)
        assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
        issued = await api.post(
            base + "/imports/apply",
            json={
                **data,
                **await command(api, base),
                "preview_hash": preview.json()["preview_hash"],
            },
        )
        assert issued.status_code == 201, issued.text
        openings = (await api.get(base + "/opening-balances")).json()["items"]
        trade = next(r for r in openings if r["kind"] == "customer")
        loan = next(r for r in openings if r["kind"] == "loan_payable")
        paid = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-10-02",
                "bank_account_id": a["bank"],
                "control_account_id": a["ar"],
                "amount": "40",
                "allocations": [{"document_id": trade["document_id"], "amount": "40"}],
            },
        )
        assert paid.status_code == 201, paid.text
        attempted = []
        for item, direction, amount in [
            (trade, "increase", "10"),
            (trade, "decrease", "20"),
            (loan, "increase", "30"),
            (loan, "decrease", "10"),
        ]:
            payload = {
                **await command(api, base),
                "accounting_date": "2026-10-03",
                "direction": direction,
                "amount": amount,
                "reason": "Reconciled source correction",
            }
            attempted.append(payload)
            corrected = await api.post(
                base + f"/opening-balances/{item['id']}/corrections",
                json=payload,
            )
            assert corrected.status_code == 201, corrected.text
        wrong_target = await api.post(
            base + f"/opening-balances/{loan['id']}/corrections", json=attempted[0]
        )
        assert wrong_target.status_code == 409 and "ERR_IDEMPOTENCY_CONFLICT" in wrong_target.text
        too_much = await api.post(
            base + f"/opening-balances/{trade['id']}/corrections",
            json={
                **await command(api, base),
                "accounting_date": "2026-10-03",
                "direction": "decrease",
                "amount": "100",
                "reason": "Must not exceed unallocated balance",
            },
        )
        assert too_much.status_code == 409
        controls = await api.get(base + "/reports/controls", params={"as_of": "2026-10-31"})
        assert controls.json()["balanced"], controls.text
        dates = {"start": "2026-10-01", "end": "2026-10-31", "basis": "cash"}
        assert (await api.get(base + "/reports/profit-loss", params=dates)).status_code == 409
        eid = await clean_upload(api, base, bid, database_settings)
        policy = {
            "treatment": "on_settlement",
            "tax_treatment": "no_tax_component",
            "components": [{"account_id": a["income"], "weight": "100"}],
            "evidence_id": eid,
            "reason": "Unrecognized cash-basis income confirmed from legacy records",
        }
        approved = await api.post(
            base + f"/opening-balances/{trade['id']}/cash-policy", json=policy
        )
        assert approved.status_code == 201, approved.text
        repeat = await api.post(base + f"/opening-balances/{trade['id']}/cash-policy", json=policy)
        assert repeat.json() == approved.json()
        pnl = await api.get(base + "/reports/profit-loss", params=dates)
        assert pnl.status_code == 200 and Decimal(pnl.json()["income"]) == 40, pnl.text
        assert approved.json()["id"] in pnl.json()["opening_cash_policy_ids"]
        balance = await api.get(
            base + "/reports/balance-sheet", params={"as_of": "2026-10-31", "basis": "cash"}
        )
        assert balance.status_code == 200 and balance.json()["balanced"], balance.text
        accrual = await api.get(base + "/reports/profit-loss", params={**dates, "basis": "accrual"})
        assert Decimal(accrual.json()["income"]) == 0
        original = await api.get(
            base + f"/trade-documents/{trade['document_id']}", params={"as_of": "2026-10-31"}
        )
        assert Decimal(original.json()["total"]) == 100
        history = await api.get(base + f"/opening-balances/{trade['id']}/history")
        assert len(history.json()["corrections"]) == 2
        assert (
            await client.post(base + f"/opening-balances/{trade['id']}/cash-policy", json=policy)
        ).status_code == 403


@pytest.mark.parametrize("kind", ["supplier", "loan_receivable"])
async def test_supplier_and_receivable_loan_corrections_and_prior_cash_treatment(
    client: httpx.AsyncClient, database_settings: FileSettings, kind: str
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        await create_account(api, base, "opening_equity", "equity")
        code = "ap" if kind == "supplier" else "loan_asset"
        if kind != "supplier":
            await create_account(api, base, code, "asset", "loan_receivable")
        data = {
            "mode": "opening",
            "cutover_date": "2026-10-01",
            "opening_equity_account_code": "opening_equity",
            "review_reason": "Reviewed legacy close",
            "expected_trial": {
                code: "-100" if kind == "supplier" else "100",
                "opening_equity": "100" if kind == "supplier" else "-100",
            },
            "content": (
                "kind,party_id,reference,original_date,due_date,amount,control_account_code\n"
                f"{kind},{party},OLD,2025-01-01,2026-01-01,100,{code}\n"
            ),
        }
        preview = await api.post(base + "/imports/preview", json=data)
        result = await api.post(
            base + "/imports/apply",
            json={
                **data,
                **await command(api, base),
                "preview_hash": preview.json()["preview_hash"],
            },
        )
        assert result.status_code == 201, result.text
        opening = (await api.get(base + "/opening-balances")).json()["items"][0]
        for direction, amount in [("increase", "20"), ("decrease", "10")]:
            result = await api.post(
                base + f"/opening-balances/{opening['id']}/corrections",
                json={
                    **await command(api, base),
                    "direction": direction,
                    "amount": amount,
                    "accounting_date": "2026-10-02",
                    "reason": "Reviewed adjustment",
                },
            )
            assert result.status_code == 201, result.text
        if kind == "supplier":
            eid = await clean_upload(api, base, bid, database_settings)
            approved = await api.post(
                base + f"/opening-balances/{opening['id']}/cash-policy",
                json={
                    "treatment": "previously_recognized",
                    "tax_treatment": "separately_accounted",
                    "components": [],
                    "evidence_id": eid,
                    "reason": "Legacy expense already recognized",
                },
            )
            assert approved.status_code == 201, approved.text
            paid = await api.post(
                base + "/payments",
                json={
                    **await command(api, base),
                    "kind": "payment",
                    "party_id": party,
                    "accounting_date": "2026-10-03",
                    "bank_account_id": a["bank"],
                    "control_account_id": a["ap"],
                    "amount": "40",
                    "allocations": [{"document_id": opening["document_id"], "amount": "40"}],
                },
            )
            assert paid.status_code == 201, paid.text
            pnl = await api.get(
                base + "/reports/profit-loss",
                params={"start": "2026-10-01", "end": "2026-10-31", "basis": "cash"},
            )
            assert pnl.status_code == 200 and Decimal(pnl.json()["expense"]) == 0, pnl.text
        controls = await api.get(base + "/reports/controls", params={"as_of": "2026-10-31"})
        assert controls.status_code == 200 and controls.json()["balanced"], controls.text
