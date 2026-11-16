"""Verify locked close package preparation and independent review."""

import os
from uuid import UUID

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_close_package_requires_independent_review(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        base = f"/api/v1/businesses/{bid}"
        period = await accountant.post(
            base + "/periods",
            json={
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            },
        )
        assert period.status_code == 201, period.text
        period_id = period.json()["id"]
        locked = await accountant.patch(
            base + f"/periods/{period_id}",
            json={
                "locked": True,
                "expected_version": 0,
            },
        )
        assert locked.status_code == 200, locked.text
        app = create_app(
            database_settings.model_copy(
                update={
                    "p2_micro_close_business_ids": frozenset({UUID(bid)}),
                }
            )
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as preparer,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=client.cookies,
                headers=dict(client.headers),
            ) as reviewer,
        ):
            package = await preparer.post(base + "/close-packages", params={"period_id": period_id})
            assert package.status_code == 201, package.text
            path = base + "/close-packages/" + package.json()["id"]
            assert (await preparer.post(path + "/review")).status_code == 403
            signed = await reviewer.post(path + "/review")
            assert signed.status_code == 200, signed.text
            stored = (await reviewer.get(path)).json()
            assert stored["review"] == signed.json()
            assert stored["package"] == package.json()
