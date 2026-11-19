"""Synthetic workspace to journal, portfolio indicators and cited answers."""

import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401
from test_phase3_integration import command, invoice, setup

from ledgerpilot.accounting.entry_tools import Depreciation, journal_preview
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.reports.accountant_tools import question_period


def test_month_boundaries_and_final_cent() -> None:
    assert question_period("net profit last month", date(2024, 3, 1)) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )
    amounts = []
    for index in (1, 2, 3):
        entry = Depreciation(
            kind="depreciation",
            reference="asset",
            accounting_date=date.today(),
            debit_account=uuid4(),
            credit_account=uuid4(),
            amount=Decimal("100"),
            residual=Decimal("10.01"),
            months=3,
            installment=index,
        )
        result = journal_preview(entry, "USD", 2)
        amounts.append(Decimal(result.lines[0].debit))
    assert sum(amounts) == Decimal("89.99")


@pytest.mark.skipif(os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Local PostgreSQL")
async def test_accountant_tools(
    client: httpx.AsyncClient,  # noqa: F811
    database_settings: FileSettings,  # noqa: F811
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        for kind in ("bill", "invoice"):
            issued = await invoice(api, base, accounts, party, kind)
            assert issued.status_code == 201
        summary = await api.get(base + "/portfolio-status")
        assert summary.status_code == 200
        assert summary.json()["bills_without_evidence"] == 1
        assert (await api.get(f"/api/v1/businesses/{uuid4()}/portfolio-status")).status_code == 404
        for question, expected in [
            ("What was our net profit last month?", "Net profit"),
            ("Which client owes us the most?", "Synthetic multi-role party"),
            ("Show me why expenses spiked in October", "Largest increase"),
        ]:
            answer = await api.post(
                base + "/insights/question",
                json={"question": question, "as_of": "2026-11-15", "basis": "accrual"},
            )
            assert answer.status_code == 200, answer.text
            assert expected in answer.json()["answer"]
            for rid in answer.json()["report_ids"]:
                assert (await api.get(base + "/reports/history/" + rid)).status_code == 200
        for fields, expected_amount in [
            ({"kind": "accrual"}, "100"),
            ({"kind": "depreciation", "residual": "10", "months": 3, "installment": 1}, "30"),
            (
                {
                    "kind": "fx",
                    "source_currency": "GBP",
                    "rate": "1.25",
                    "rate_source": "Synthetic",
                },
                "125",
            ),
        ]:
            preview = await api.post(
                base + "/accounting/entry-preview",
                json={
                    **fields,
                    "amount": "100",
                    "reference": "Synthetic entry",
                    "accounting_date": "2026-11-01",
                    "debit_account": accounts["cost"],
                    "credit_account": accounts["bank"],
                },
            )
            assert preview.status_code == 200, preview.text
            assert Decimal(preview.json()["lines"][0]["debit"]) == Decimal(expected_amount)
            payload = {**preview.json(), **await command(api, base)}
            posted = await api.post(base + "/journals", json=payload)
            assert posted.status_code == 201, posted.text
            assert (await api.post(base + "/journals", json=payload)).json() == posted.json()
