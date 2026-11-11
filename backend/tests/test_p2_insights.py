"""Verify real report snapshots, scenario arithmetic and isolation."""

import os
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import invoice, setup

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# ruff: noqa: F811 -- shared service-backed fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_cash_stress_and_cited_profit_comparison(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        for kind in ("invoice", "bill"):
            issued = await invoice(api, base, accounts, party, kind)
            assert issued.status_code == 201, issued.text

        path = base + "/reports/forecast"
        params = {"as_of": "2026-10-31", "bank_ids": accounts["bank"]}
        baseline = await api.get(path, params=params)
        assert baseline.status_code == 200, baseline.text
        assert Decimal(baseline.json()["weeks"][0]["closing_cash"]) == 0
        assert "scenario" not in baseline.json()
        assert (await api.get(path, params={**params, "receipt_percent": 75})).status_code == 404

        settings = database_settings.model_copy(
            update={"p2_insight_business_ids": frozenset({UUID(bid)})}
        )
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=api.cookies,
                headers=dict(api.headers),
            ) as enabled,
        ):
            assert (await enabled.get(base)).json()["p2_insights_available"]
            stress = await enabled.get(
                path, params={**params, "receipt_percent": 75, "payment_percent": 125}
            )
            assert stress.status_code == 200, stress.text
            forecast = stress.json()
            assert forecast["scenario"] == {"receipt_percent": 75, "payment_percent": 125}
            assert forecast["first_shortfall"] == "2026-11-07"
            assert Decimal(forecast["weeks"][0]["closing_cash"]) == -50
            assert Decimal(forecast["weeks"][-1]["closing_cash"]) == -50
            assert (
                forecast["weeks"][0]["document_ids"] == baseline.json()["weeks"][0]["document_ids"]
            )
            assert (
                await enabled.get(path, params={**params, "receipt_percent": 201})
            ).status_code == 422
            saved = await enabled.get(base + "/reports/history/" + forecast["report_id"])
            assert saved.status_code == 200, saved.text
            assert saved.json()["scenario"] == forecast["scenario"]
            for report, metric, expected in (
                (forecast, "forecast_first_shortfall", "2026-11-07"),
                (forecast, "forecast_closing_cash", forecast["weeks"][-1]["closing_cash"]),
                (baseline.json(), "forecast_first_shortfall", None),
            ):
                question = {"report_id": report["report_id"], "metric": metric}
                answer = await enabled.post(base + "/assistant/report-answer", json=question)
                assert answer.status_code == 200, answer.text
                assert answer.json()["report_id"] == report["report_id"]
                assert answer.json()["value"] == expected
                assert not answer.json()["actions_executed"]

            reports = []
            for start, end in [("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-30")]:
                report = await enabled.get(
                    base + "/reports/profit-loss", params={"start": start, "end": end}
                )
                assert report.status_code == 200, report.text
                reports.append(report.json()["report_id"])
            query = {"current_report_id": reports[1], "previous_report_id": reports[0]}
            variance_path = base + "/reports/profit-variance"
            assert (await api.get(variance_path, params=query)).status_code == 404
            response = await enabled.get(variance_path, params=query)
            assert response.status_code == 200, response.text
            variance = response.json()
            assert variance["current_report_id"] == reports[1]
            assert variance["previous_report_id"] == reports[0]
            assert Decimal(variance["net_profit_change"]) == 0
            assert Decimal(variance["income_change"]) == Decimal(variance["expense_change"]) == 100
            assert {
                row["account_id"]: Decimal(row["contribution"]) for row in variance["drivers"]
            } == {accounts["income"]: Decimal(100), accounts["cost"]: Decimal(-100)}
            missing = await enabled.get(
                variance_path, params={**query, "previous_report_id": uuid4()}
            )
            assert missing.status_code == 404
            overlap = await enabled.get(
                variance_path, params={**query, "previous_report_id": reports[1]}
            )
            assert overlap.status_code == 422
