"""Frontend API contract acceptance and authorized numeric-answer boundaries."""

import os
from decimal import Decimal
from uuid import uuid4

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

# ruff: noqa: F811 -- real imported fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires real PostgreSQL/SMTP"
)


def test_every_http_success_and_error_has_an_explicit_schema(
    database_settings: FileSettings,
) -> None:
    spec = create_app(database_settings).openapi()
    ids = []
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "patch"}:
                continue
            ids.append(operation["operationId"])
            for code, response in operation["responses"].items():
                if code.startswith("2"):
                    content = response.get("content", {})
                    assert content, (path, method, code)
                    assert all(media.get("schema") for media in content.values()), (
                        path,
                        method,
                        code,
                    )
            if path.startswith("/api/v1"):
                assert {"401", "403", "409", "422", "429", "503"} <= operation["responses"].keys()
    assert len(ids) == len(set(ids))


async def test_reports_workspace_lists_and_cited_answers_are_frontend_usable(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        issued = await invoice(api, base, a, party)
        assert issued.status_code == 201, issued.text
        dates = {"start": "2026-10-01", "end": "2026-10-31"}
        reads = [
            ("/dashboard", {**dates, "bank_ids": a["bank"]}),
            ("/reports/forecast", {"as_of": "2026-10-01", "bank_ids": a["bank"]}),
            ("/reports/tax-support", dates),
            ("/reports/aging", {"as_of": "2026-10-31"}),
            ("/reports/balance-sheet", {"as_of": "2026-10-31"}),
            ("/readiness", {"as_of": "2026-10-31"}),
        ]
        snapshots = []
        for path, params in reads:
            result = await api.get(base + path, params=params)
            assert result.status_code == 200, (path, result.text)
            snapshots.append(result.json()["report_id"])
            saved = await api.get(base + f"/reports/history/{snapshots[-1]}")
            assert saved.status_code == 200 and saved.json() == result.json(), saved.text
        for path in [
            "/bank-rules",
            "/source-drafts",
            "/imports",
            "/opening-balances",
            "/beneficiary-changes",
            "/notifications",
            "/notification-preferences",
            "/audit-events",
            "/reports/history",
            "/jobs",
            "/consents",
            "/privacy-requests",
            "/privacy-review-queue",
            "/evidence",
            "/usage",
        ]:
            result = await api.get(base + path)
            assert result.status_code == 200, (path, result.text)
        forecast = await api.get(
            base + "/reports/forecast", params={"as_of": "2026-10-01", "bank_ids": a["bank"]}
        )
        assert len(forecast.json()["weeks"]) == 13 and forecast.json()["bands"] is None
        assert sum(Decimal(w["receipts"]) for w in forecast.json()["weeks"]) == 100
        pnl = await api.get(base + "/reports/profit-loss", params=dates)
        answer = await api.post(
            base + "/assistant/report-answer",
            json={"report_id": pnl.json()["report_id"], "metric": "income"},
        )
        assert answer.status_code == 200 and Decimal(answer.json()["value"]) == 100, answer.text
        assert not answer.json()["actions_executed"]
        missing = await api.post(
            base + "/assistant/report-answer", json={"report_id": str(uuid4()), "metric": "income"}
        )
        assert missing.status_code == 404
        injected = await api.post(
            base + "/assistant/report-answer",
            json={"report_id": pnl.json()["report_id"], "metric": "DROP TABLE journals"},
        )
        assert injected.status_code == 422
        duplicate = await api.get(
            base + "/reports/forecast",
            params=[("as_of", "2026-10-01"), ("bank_ids", a["bank"]), ("bank_ids", a["bank"])],
        )
        assert duplicate.status_code == 400, duplicate.text
        overflow = await api.get(
            base + "/reports/forecast", params={"as_of": "9999-12-31", "bank_ids": a["bank"]}
        )
        assert overflow.status_code == 422
        preference = await api.patch(
            base + "/notification-preferences",
            json={
                "expected_version": 0,
                "marketing": False,
                "reminders": True,
                "quiet_start": 22,
                "quiet_end": 7,
            },
        )
        assert preference.status_code == 200 and preference.json()["version"] == 1, preference.text
        stale = await api.patch(base + "/notification-preferences", json={"expected_version": 0})
        assert stale.status_code == 409
        reminder = await api.post(base + "/reminders/review", json={"as_of": "2026-11-02"})
        assert reminder.status_code == 200 and reminder.json()["external_messages_sent"] == 0
        notifications = (await api.get(base + "/notifications")).json()["items"]
        assert notifications
        marked = await api.post(base + f"/notifications/{notifications[0]['id']}/read")
        assert marked.status_code == 200 and marked.json()["read"]
