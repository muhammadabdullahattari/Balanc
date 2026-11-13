"""Verify real report snapshots, scenario arithmetic and isolation."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.orm import sessionmaker
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import command, invoice, setup

from ledgerpilot.database import build_engine, set_context
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.reports import service
from ledgerpilot.workspace.models import ReportRun

# ruff: noqa: F811 -- shared service-backed fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_cash_stress_and_cited_profit_comparison(
    client: httpx.AsyncClient, database_settings: FileSettings, monkeypatch: pytest.MonkeyPatch
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

            evaluation = path + "/{}/evaluation"
            assert (await enabled.get(evaluation.format(forecast["report_id"]))).status_code == 422
            assert (await enabled.get(evaluation.format(uuid4()))).status_code == 404
            # Seed a synthetic baseline captured on its forecast date. Live runs
            # always use the database capture timestamp and cannot be backdated.
            original = baseline.json()
            historical_id = uuid4()
            actor = UUID((await enabled.get("/api/v1/auth/me")).json()["id"])
            engine = build_engine(settings)
            try:
                with sessionmaker(engine).begin() as db:
                    set_context(db, actor, UUID(bid))
                    db.add(
                        ReportRun(
                            id=historical_id,
                            business_id=UUID(bid),
                            created_by=actor,
                            created_at=datetime(2026, 10, 31, 18, tzinfo=UTC),
                            kind="deterministic_cash_plan",
                            result={**original, "report_id": str(historical_id)},
                        )
                    )
            finally:
                engine.dispose()
            journal = await enabled.post(
                base + "/journals",
                json={
                    **await command(enabled, base),
                    "accounting_date": "2026-11-03",
                    "currency": "USD",
                    "memo": "Observed cash",
                    "lines": [
                        {"account_id": accounts["bank"], "debit": "42"},
                        {"account_id": accounts["income"], "credit": "42"},
                    ],
                },
            )
            assert journal.status_code == 201, journal.text
            monkeypatch.setattr(service, "now", lambda: datetime(2026, 11, 8, 18, tzinfo=UTC))
            result = await enabled.get(evaluation.format(historical_id))
            assert result.status_code == 200, result.text
            assert result.json()["method"] == "ledger_cash_error_v1"
            assert Decimal(result.json()["mean_absolute_error"]) == 42
            weeks = result.json()["weeks"]
            assert len(weeks) == 1 and weeks[0]["end_date"] == "2026-11-07"
            assert Decimal(weeks[0]["forecast_cash"]) == 0
            assert Decimal(weeks[0]["ledger_cash"]) == 42
            assert Decimal(weeks[0]["difference"]) == 42

            interval = await enabled.get(
                base + f"/reports/forecast/{historical_id}/interval", params={"week": 1}
            )
            assert interval.status_code == 200, interval.text
            assert interval.json()["status"] == "insufficient_history"
            assert interval.json()["lower_cash"] is None
            # Synthetic, chronologically separated holdouts exercise the complete SQL path.
            engine = build_engine(settings)
            try:
                with sessionmaker(engine).begin() as db:
                    set_context(db, actor, UUID(bid))
                    for index in range(30):
                        captured = datetime(2026, 10, 31, 18, tzinfo=UTC) - timedelta(
                            days=(index + 1) * 8
                        )
                        sid = uuid4()
                        week = {
                            **original["weeks"][0],
                            "start_date": str(captured.date() + timedelta(days=1)),
                            "end_date": str(captured.date() + timedelta(days=7)),
                            "closing_cash": "5",
                        }
                        db.add(
                            ReportRun(
                                id=sid,
                                business_id=UUID(bid),
                                created_by=actor,
                                created_at=captured,
                                kind="deterministic_cash_plan",
                                result={
                                    **original,
                                    "report_id": str(sid),
                                    "as_of": str(captured.date()),
                                    "weeks": [week],
                                },
                            )
                        )
            finally:
                engine.dispose()
            calibrated = await enabled.get(
                base + f"/reports/forecast/{historical_id}/interval", params={"week": 1}
            )
            assert calibrated.status_code == 200, calibrated.text
            assert calibrated.json()["status"] == "available"
            assert calibrated.json()["calibration_count"] == 20
            assert calibrated.json()["validation_count"] == 10
            assert Decimal(calibrated.json()["lower_cash"]) == -5
            assert Decimal(calibrated.json()["upper_cash"]) == 5
            assert (
                await enabled.get(
                    base + f"/reports/forecast/{historical_id}/interval", params={"week": 14}
                )
            ).status_code == 422

            statement = {
                "account_id": accounts["bank"],
                "content": "date,description,amount\n2026-11-04,Fee A,-12\n2026-11-05,Fee B,-13\n",
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                },
            }
            preview = await enabled.post(base + "/bank-imports/preview", json=statement)
            assert preview.status_code == 200, preview.text
            imported = await enabled.post(
                base + "/bank-imports",
                json={**statement, "expected_hash": preview.json()["file_hash"]},
            )
            assert imported.status_code == 201, imported.text
            bank_rows = (await enabled.get(base + "/bank-transactions")).json()["items"]
            disagreeing_row = next(row["id"] for row in bank_rows if row["description"] == "Fee B")
            for row in bank_rows:
                category = (
                    accounts["cost"] if row["description"] == "Fee A" else accounts["interest"]
                )
                disposition = await enabled.post(
                    base + f"/bank-transactions/{row['id']}/disposition",
                    json={
                        **await command(enabled, base),
                        "expected_row_version": 0,
                        "action": "categorize",
                        "reason": "Synthetic review",
                        "splits": [
                            {"account_id": category, "amount": str(abs(Decimal(row["amount"])))}
                        ],
                    },
                )
                assert disposition.status_code == 200, disposition.text
            draft = await enabled.post(
                base + "/bank-rules",
                json={"name": "Fee category", "contains": "Fee", "account_id": accounts["cost"]},
            )
            assert draft.status_code == 201, draft.text
            rule_id = draft.json()["id"]
            shadow = base + f"/bank-rules/{rule_id}/shadow-evaluation"
            parameters = {"start": "2026-11-01", "end": "2026-11-30", "max_amount": "20"}
            assert (await api.get(shadow, params=parameters)).status_code == 404
            evaluated = await enabled.get(shadow, params=parameters)
            assert evaluated.status_code == 200, evaluated.text
            assert evaluated.json() == {
                "rule_id": rule_id,
                "rule_version": 0,
                "evaluated": 2,
                "matched": 1,
                "mismatched_row_ids": [disagreeing_row],
            }
            capped = await enabled.get(shadow, params={**parameters, "max_amount": "12"})
            assert capped.status_code == 200 and capped.json()["matched"] == 1
            assert capped.json()["evaluated"] == 1
