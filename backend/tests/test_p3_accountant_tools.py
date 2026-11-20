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
    monkeypatch: pytest.MonkeyPatch,
    client: httpx.AsyncClient,  # noqa: F811
    database_settings: FileSettings,  # noqa: F811
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        listing = await api.get(
            "/api/v1/businesses", params={"sort": "name", "search": "unmatched%_"}
        )
        assert listing.status_code == 200 and not listing.json()["items"]
        listing = await api.get("/api/v1/businesses", params={"sort": "name", "limit": 1})
        assert listing.status_code == 200 and len(listing.json()["items"]) == 1
        quote = {"day": "2026-12-31", "currency": "GBP"}
        from ledgerpilot.reports import accountant_tools

        calls = []

        def provider(url, lookup, currency):
            calls.append(currency)
            return Decimal("1.25")

        monkeypatch.setattr(accountant_tools, "fetch_quote", provider)
        for _ in range(2):
            rate = await api.post(base + "/accounting/fx-rates/lookup", json=quote)
            assert rate.status_code == 200, rate.text
            assert Decimal(rate.json()["rate"]) == Decimal("1.25")
        assert len(calls) == 1
        overridden = await api.post(
            base + "/accounting/fx-rates/override",
            json={**quote, "source": "Documented closing rate", "rate": "1.26"},
        )
        assert overridden.status_code == 200
        rate = await api.post(base + "/accounting/fx-rates/lookup", json=quote)
        assert Decimal(rate.json()["rate"]) == Decimal("1.26")
        proposal = {
            "period_end": "2026-12-31",
            "currency": "GBP",
            "foreign_units": "100",
            "closing_rate": "1.26",
            "carrying_balance": "120",
            "rate_source": "Documented closing rate",
            "monetary_account": accounts["bank"],
            "gain_account": accounts["income"],
            "loss_account": accounts["cost"],
        }
        first = await api.post(base + "/accounting/revaluation-preview", json=proposal)
        assert first.status_code == 200, first.text
        second = await api.post(base + "/accounting/revaluation-preview", json=proposal)
        assert second.json() == first.json()
        assert Decimal(first.json()["lines"][0]["debit"]) == 6
        assert (
            await api.post(
                base + "/accounting/revaluation-preview", json={**proposal, "closing_rate": "1.29"}
            )
        ).status_code == 409
        # A proposal does not move the ledger. Only explicit approved posting does.
        before = (await api.get(base + "/trial-balance", params={"as_of": "2026-12-31"})).json()
        payload = {**first.json(), **await command(api, base)}
        payload["idempotency_key"] = f"fx.2026-12-31.GBP.{accounts['bank']}"
        posted = await api.post(base + "/journals", json=payload)
        assert posted.status_code == 201, posted.text
        assert (await api.post(base + "/journals", json=payload)).json() == posted.json()
        journal_id = posted.json()["id"]
        journal = (await api.get(base + "/journals/" + journal_id)).json()
        assert sum(Decimal(x["debit"]) - Decimal(x["credit"]) for x in journal["lines"]) == 0
        reversed_entry = await api.post(
            base + "/journals/" + journal_id + "/reversal",
            json={
                **await command(api, base),
                "accounting_date": "2026-12-31",
                "memo": "Reverse synthetic FX valuation",
            },
        )
        assert reversed_entry.status_code == 201, reversed_entry.text
        after = (await api.get(base + "/trial-balance", params={"as_of": "2026-12-31"})).json()
        assert {x["account_id"]: Decimal(x["balance"]) for x in before["items"]} == {
            x["account_id"]: Decimal(x["balance"]) for x in after["items"]
        }
        denied = await api.get(f"/api/v1/businesses/{uuid4()}/journals/{journal_id}")
        assert denied.status_code == 404
        created = await api.post(
            base + "/accounting/assets",
            json={
                "name": "Press",
                "acquired": "2024-02-29",
                "cost": "150.00",
                "residual": "30",
                "months": 12,
            },
        )
        assert created.status_code == 201, created.text
        asset_id = created.json()["id"]
        assets = (await api.get(base + "/accounting/assets")).json()
        assert assets[0]["id"] == asset_id
        schedule = (await api.get(base + "/accounting/assets/" + asset_id + "/schedule")).json()
        assert schedule[0]["date"] == "2024-02-29"
        assert sum(Decimal(item["amount"]) for item in schedule) == Decimal("120")
        disposed = await api.patch(
            base + "/accounting/assets/" + asset_id,
            json={"name": "Press", "expected_version": 1, "disposed_on": "2026-10-31"},
        )
        assert disposed.status_code == 200, disposed.text
        period = await api.post(
            base + "/periods", json={"start_date": "2024-01-01", "end_date": "2024-12-31"}
        )
        assert period.status_code == 201, period.text
        for action in ("acquisition", "disposal"):
            proposed = await api.post(
                base + f"/accounting/assets/{asset_id}/lifecycle-preview",
                json={
                    "action": action,
                    "debit_account": accounts["bank"]
                    if action == "acquisition"
                    else accounts["cost"],
                    "credit_account": accounts["income"]
                    if action == "acquisition"
                    else accounts["bank"],
                    "carrying_value": "30" if action == "disposal" else None,
                },
            )
            assert proposed.status_code == 200, proposed.text
            approved = {**proposed.json(), **await command(api, base)}
            approved["idempotency_key"] = f"asset.{asset_id}.{action}"
            entry = await api.post(base + "/journals", json=approved)
            assert entry.status_code == 201, entry.text
            detail = (await api.get(base + "/journals/" + entry.json()["id"])).json()
            assert detail["asset_id"] == asset_id
        linked = (await api.get(base + f"/accounting/assets/{asset_id}/journals")).json()
        assert len(linked) == 2
        for kind in ("bill", "invoice"):
            issued = await invoice(api, base, accounts, party, kind)
            assert issued.status_code == 201
            if kind == "bill":
                doc = issued.json()["id"]
                assignee = (await api.get("/api/v1/auth/me")).json()["id"]
                endpoint = f"{base}/portfolio-exceptions/bill/{doc}"
                for previous, target in [(None, assignee), (assignee, None)]:
                    changed = await api.post(
                        endpoint + "/assignment",
                        json={"assignee": target, "expected_assignee": previous},
                    )
                    assert changed.status_code == 200, changed.text
                    assert changed.json()["assignee"] == target
                history = (await api.get(endpoint + "/assignment-history")).json()
                assert [(event["previous_user"], event["assigned_user"]) for event in history] == [
                    (assignee, None),
                    (None, assignee),
                ]
        summary = await api.get(base + "/portfolio-status")
        assert summary.status_code == 200
        assert summary.json()["bills_without_evidence"] == 1
        assert (await api.get(f"/api/v1/businesses/{uuid4()}/portfolio-status")).status_code == 404
        for question, expected in [
            ("What was our net profit last month?", "Net profit"),
            ("Which client owes us the most?", "Synthetic multi-role party"),
            ("Show me why expenses spiked in October", "Largest increase"),
            ("show me revenue in October", "Revenue from 2026-10-01"),
            (
                "explain net profit change versus previous month in October",
                "Contributions to net profit change",
            ),
            ("show me expenses in October", "Expenses from 2026-10-01"),
            (
                "compare revenue with previous quarter in January 2026",
                "2025-10-01 through 2025-12-31",
            ),
            ("show top expense accounts in February 2024", "2024-02-29"),
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


@pytest.mark.parametrize(
    "units,rate,carrying,expected",
    [
        ("100", "1.25", "120", "5"),
        ("-100", "1.25", "-120", "-5"),
        ("0", "1.25", "0.01", "-0.01"),
        ("100", "1.25", "125", "0"),
    ],
)
def test_revaluation_amount(units: str, rate: str, carrying: str, expected: str) -> None:
    from ledgerpilot.accounting.entry_tools import revaluation_delta

    assert revaluation_delta(Decimal(units), Decimal(rate), Decimal(carrying), 2) == Decimal(
        expected
    )


def test_quote_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    from ledgerpilot.reports.accountant_tools import QuoteLookup, fetch_quote
    from ledgerpilot.shared.errors import DomainError

    def offline(*args, **kwargs):
        raise httpx.ConnectTimeout("offline")

    monkeypatch.setattr(httpx, "get", offline)
    with pytest.raises(DomainError, match="ERR_PROVIDER") as error:
        fetch_quote(
            "https://provider.invalid", QuoteLookup(day=date(2024, 2, 29), currency="GBP"), "USD"
        )
    assert error.value.status == 503 and "Could not verify" in error.value.message
