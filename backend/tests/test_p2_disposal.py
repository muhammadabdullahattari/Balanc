"""Test mixed-selection rollback and holds applied after a disposal preview."""

import os
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401

from ledgerpilot.database import set_context
from ledgerpilot.identity.security import now
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.workspace.models import ExportSnapshot

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and SMTP"
)


async def test_disposal_is_atomic_and_rechecks_holds(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        business_id = UUID(bid)
        actor = UUID((await accountant.get("/api/v1/auth/me")).json()["id"])
        settings = database_settings.model_copy(
            update={
                "p2_retention_business_ids": frozenset({business_id}),
                "p2_export_disposal_enabled": True,
                "p2_export_disposal_approval": "synthetic test only",
            }
        )
        app = create_app(settings)
        base = f"/api/v1/businesses/{bid}/retention"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as api,
        ):
            with app.state.sessions.begin() as db:
                set_context(db, actor, business_id)
                rows = [
                    ExportSnapshot(
                        business_id=business_id,
                        created_by=actor,
                        key=f"test-disposal-key-{days}",
                        request_hash="0" * 64,
                        format="journals_csv",
                        business_version=0,
                        digest="0" * 64,
                        size=1,
                        payload=b"x",
                        expires_at=now() + timedelta(days=days),
                    )
                    for days in (-1, 1)
                ]
                db.add_all(rows)
                db.flush()
                expired, current = (str(row.id) for row in rows)
            path = base + "/disposal/exports"
            assert (await api.get(path)).json()["items"][0]["decision"] == "eligible"
            mixed = await api.post(path, json={"ids": [expired, current]})
            assert mixed.status_code == 409, mixed.text
            hold = await api.post(base + "/holds", json={"category": "all", "reason": "Test hold"})
            assert hold.status_code == 201, hold.text
            blocked = await api.post(path, json={"ids": [expired]})
            assert (
                blocked.status_code == 409 and blocked.json()["error"]["code"] == "ERR_LEGAL_HOLD"
            )
            released = await api.post(
                base + f"/holds/{hold.json()['id']}/release", json={"reason": "Test release"}
            )
            assert released.status_code == 200, released.text
            deleted = await api.post(path, json={"ids": [expired]})
            assert deleted.status_code == 200 and deleted.json()["deleted"] == [expired]
            assert [item["id"] for item in (await api.get(path)).json()["items"]] == [current]
