"""Exercise immutable nightly workpapers with real PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import setup

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace import micro_close

# ruff: noqa: F811 -- shared service-backed fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_micro_close_captures_one_immutable_workspace_report(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = datetime(2026, 11, 2, 1, tzinfo=UTC)
    monkeypatch.setattr(micro_close, "now", lambda: clock)
    async with accountant_context(client, database_settings) as (bid, accountant):
        base, accounts, _ = await setup(accountant, bid)
        business = UUID(bid)
        actor = UUID((await accountant.get("/api/v1/auth/me")).json()["id"])
        settings = database_settings.model_copy(
            update={"p2_micro_close_business_ids": frozenset({business})}
        )
        with pytest.raises(DomainError, match="ERR_NOT_FOUND"):
            micro_close.run_once(database_settings, actor, business)
        with pytest.raises(DomainError, match="ERR_NOT_FOUND"):
            await asyncio.to_thread(micro_close.run_once, settings, uuid4(), business)
        endpoint = base + "/micro-close"
        params = {"as_of": "2026-10-31"}
        assert (await accountant.get(endpoint, params=params)).status_code == 404
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as enabled,
        ):
            empty = await enabled.get(endpoint, params=params)
            assert empty.status_code == 200 and empty.json() == {"workpaper": None}
            first, second = await asyncio.gather(
                asyncio.to_thread(micro_close.run_once, settings, actor, business),
                asyncio.to_thread(micro_close.run_once, settings, actor, business),
            )
            assert sorted([first["created"], second["created"]]) == [False, True]
            assert first["as_of"] == second["as_of"] == params["as_of"]
            assert first["report_id"] == second["report_id"]
            stored = await enabled.get(endpoint, params=params)
            assert stored.status_code == 200, stored.text
            snapshot = stored.json()["workpaper"]["report"]
            assert snapshot["ready"] and snapshot["unreviewed_bank_rows"] == 0
            assert snapshot["report_id"] == first["report_id"]
            for report in (snapshot, snapshot["controls"], snapshot["tax_controls"]):
                cited = await enabled.get(base + "/reports/history/" + report["report_id"])
                assert cited.status_code == 200, cited.text
            audit = await enabled.get(base + "/audit-events", params={"limit": 200})
            assert audit.status_code == 200, audit.text
            assert [
                event["resource_id"]
                for event in audit.json()["items"]
                if event["action"] == "close.micro_close_captured"
            ] == [first["report_id"]]

            bank_import = {
                "account_id": accounts["bank"],
                "content": "date,description,amount\n2026-10-31,Late import,50\n",
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                },
            }
            preview = await accountant.post(base + "/bank-imports/preview", json=bank_import)
            assert preview.status_code == 200, preview.text
            imported = await accountant.post(
                base + "/bank-imports",
                json={**bank_import, "expected_hash": preview.json()["file_hash"]},
            )
            assert imported.status_code == 201, imported.text
            assert (await enabled.get(endpoint, params=params)).json() == stored.json()
            polls = 0

            def advance(seconds: int) -> None:
                nonlocal clock, polls
                assert seconds == 60
                polls += 1
                if polls == 2:
                    raise InterruptedError("Stop the test worker")
                clock = datetime(2026, 11, 2, 12, tzinfo=UTC)

            monkeypatch.setattr(micro_close, "sleep", advance)
            with pytest.raises(InterruptedError):
                await asyncio.to_thread(micro_close.watch, settings, actor, business)
            assert polls == 2
            new_workpaper = await enabled.get(endpoint, params={"as_of": "2026-11-01"})
            assert new_workpaper.status_code == 200, new_workpaper.text
            updated = new_workpaper.json()["workpaper"]["report"]
            assert not updated["ready"] and updated["unreviewed_bank_rows"] == 1
