"""Real cash alerts retain source citations, preference controls and replay safety."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401
from test_phase3_integration import invoice, setup

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.notifications import shortfalls

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_cash_alert_replay_quiet_hours_and_report(
    client: httpx.AsyncClient, database_settings: FileSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        assert (await invoice(api, base, accounts, party, "bill")).status_code == 201
        uid = UUID((await api.get("/api/v1/auth/me")).json()["id"])
        settings = database_settings.model_copy(
            update={"p2_insight_business_ids": frozenset({UUID(bid)})}
        )
        # setup() creates a New York workspace; noon UTC is outside quiet hours.
        monkeypatch.setattr(shortfalls, "now", lambda: datetime(2026, 10, 31, 16, tzinfo=UTC))
        preferences = (await api.get(base + "/notification-preferences")).json()
        saved = await api.patch(
            base + "/notification-preferences",
            json={
                "expected_version": preferences["version"],
                "reminders": True,
                "marketing": False,
                "quiet_start": 21,
                "quiet_end": 8,
            },
        )
        assert saved.status_code == 200, saved.text
        args = (settings, uid, UUID(bid), [UUID(accounts["bank"])])
        first, second = await asyncio.gather(
            asyncio.to_thread(shortfalls.run_once, *args),
            asyncio.to_thread(shortfalls.run_once, *args),
        )
        assert sorted([first["created"], second["created"]]) == [False, True]
        assert first["report_id"] == second["report_id"]
        report = await api.get(base + "/reports/history/" + first["report_id"])
        assert report.status_code == 200, report.text
        assert report.json()["weeks"][0]["shortfall"]
        notifications = (await api.get(base + "/notifications")).json()["items"]
        alerts = [n for n in notifications if n["kind"] == "cash_shortfall"]
        assert len(alerts) == 1 and alerts[0]["resource_id"] == first["report_id"]
        monkeypatch.setattr(shortfalls, "now", lambda: datetime(2026, 11, 1, 5, tzinfo=UTC))
        assert (await asyncio.to_thread(shortfalls.run_once, *args))[
            "reason"
        ] == "preferences_or_quiet_hours"
