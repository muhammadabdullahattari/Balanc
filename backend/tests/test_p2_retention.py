"""Exercise retention versioning, legal holds and isolation on real PostgreSQL."""

import os
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_retention_version_hold_release_and_tenant_boundary(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        base = f"/api/v1/businesses/{bid}/retention"
        assert (await accountant.get(base)).status_code == 404
        settings = database_settings.model_copy(
            update={"p2_retention_business_ids": frozenset({UUID(bid)})}
        )
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as api,
        ):
            assert (await api.get(base)).json()["default_years"] == 10
            sample = {
                "category": "evidence",
                "jurisdiction": "us",
                "record_date": "2016-02-29",
                "as_of": "2026-02-28",
            }
            result = await api.post(base + "/preview", json=sample)
            assert result.status_code == 200, result.text
            assert result.json()["retain_until"] == "2026-03-01"
            assert not result.json()["retention_elapsed"]
            sample["as_of"] = "2026-03-01"
            assert (await api.post(base + "/preview", json=sample)).json()["retention_elapsed"]
            policy = {
                "category": "evidence",
                "jurisdiction": "us",
                "years": 12,
                "reference": "Approved test policy",
                "expected_version": 0,
            }
            saved = await api.post(base + "/policies", json=policy)
            assert saved.status_code == 201, saved.text
            assert saved.json()["jurisdiction"] == "US"
            assert (await api.post(base + "/policies", json=policy)).status_code == 409
            sample["as_of"] = "2029-03-01"
            assessed = (await api.post(base + "/preview", json=sample)).json()
            assert assessed["policy_id"] == saved.json()["id"]
            assert assessed["eligible_for_disposal_review"] and not assessed["deletion_authorized"]
            hold = await api.post(base + "/holds", json={"category": "all", "reason": "Case 42"})
            assert hold.status_code == 201, hold.text
            hid = hold.json()["id"]
            assessed = (await api.post(base + "/preview", json=sample)).json()
            assert assessed["active_hold_ids"] == [hid]
            assert not assessed["eligible_for_disposal_review"]
            denied = await api.post(
                f"/api/v1/businesses/{uuid4()}/retention/holds/{hid}/release",
                json={"reason": "Denied"},
            )
            assert denied.status_code == 404
            released = await api.post(
                base + f"/holds/{hid}/release", json={"reason": "Case closed"}
            )
            assert released.status_code == 200, released.text
            repeated = await api.post(base + f"/holds/{hid}/release", json={"reason": "Duplicate"})
            assert repeated.json() == released.json()
            assessed = (await api.post(base + "/preview", json=sample)).json()
            assert assessed["eligible_for_disposal_review"] and assessed["active_hold_ids"] == []
            state = (await api.get(base)).json()
            assert len(state["policies"]) == len(state["holds"]) == 1
