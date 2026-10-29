"""A card settlement clears debt without creating a second purchase expense."""

import os
from decimal import Decimal

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import command, create_account, setup

from ledgerpilot.local_setup import FileSettings

# ruff: noqa: F811 -- real fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires real PostgreSQL/SMTP"
)


async def test_paired_card_settlement_and_invalid_direction_do_not_duplicate_expense(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        card = await create_account(api, base, "card", "liability")
        purchase = await api.post(
            base + "/journals",
            json={
                **await command(api, base),
                "accounting_date": "2026-10-01",
                "memo": "Actual card purchase",
                "currency": "USD",
                "lines": [
                    {"account_id": a["cost"], "debit": "50", "credit": "0"},
                    {"account_id": card, "debit": "0", "credit": "50"},
                ],
            },
        )
        assert purchase.status_code == 201, purchase.text
        rows = []
        for account, amount in [(a["bank"], "-50"), (card, "50")]:
            data = {
                "account_id": account,
                "content": f"date,description,amount\n2026-10-02,Card settlement,{amount}\n",
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                },
            }
            preview = await api.post(base + "/bank-imports/preview", json=data)
            imported = await api.post(
                base + "/bank-imports", json={**data, "expected_hash": preview.json()["file_hash"]}
            )
            assert imported.status_code == 201, imported.text
            row = (
                await api.get(base + "/bank-transactions", params={"account_id": account})
            ).json()["items"][0]
            rows.append(row)
        settled = await api.post(
            base + f"/bank-transactions/{rows[0]['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "card_payment",
                "reason": "Reviewed both statement sides",
                "target_account_id": card,
                "peer_id": rows[1]["id"],
            },
        )
        assert settled.status_code == 200, settled.text
        trial = (await api.get(base + "/trial-balance", params={"as_of": "2026-10-31"})).json()
        values = {
            r["account_id"]: Decimal(r["debit"]) - Decimal(r["credit"]) for r in trial["items"]
        }
        assert values[card] == 0 and values[a["cost"]] == 50 and values[a["bank"]] == -50
        duplicate = await api.post(
            base + f"/bank-transactions/{rows[1]['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "card_payment",
                "reason": "Already consumed",
                "target_account_id": a["bank"],
            },
        )
        assert duplicate.status_code == 409, duplicate.text
        wrong = {
            "account_id": a["bank"],
            "content": "date,description,amount\n2026-10-03,Wrong direction,10\n",
            "mapping": {
                "date_column": "date",
                "description_column": "description",
                "amount_column": "amount",
            },
        }
        preview = await api.post(base + "/bank-imports/preview", json=wrong)
        imported = await api.post(
            base + "/bank-imports", json={**wrong, "expected_hash": preview.json()["file_hash"]}
        )
        assert imported.status_code == 201
        row = (await api.get(base + "/bank-transactions")).json()["items"][0]
        rejected = await api.post(
            base + f"/bank-transactions/{row['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "card_payment",
                "reason": "Wrong bank direction must stay unposted",
                "target_account_id": card,
            },
        )
        assert rejected.status_code == 400, rejected.text
