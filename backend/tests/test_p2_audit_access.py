"""Scoped workpapers are readable without membership and stop on revocation."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_phase2_integration import (
    ORIGIN,
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
    new_identity,
)
from test_phase3_integration import setup

from ledgerpilot.database import build_engine, set_context
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.workspace.audit_access import AuditGrant
from ledgerpilot.workspace.models import ReportRun

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_scoped_auditor_without_membership_and_revocation(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        base, _, _ = await setup(accountant, bid)
        report = await accountant.get(base + "/reports/controls", params={"as_of": "2026-10-31"})
        assert report.status_code == 200, report.text
        rid = report.json()["report_id"]
        settings = database_settings.model_copy(
            update={"p2_audit_business_ids": frozenset({UUID(bid)})}
        )
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                headers=dict(accountant.headers),
                cookies=accountant.cookies,
            ) as owner,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                headers={"Origin": ORIGIN},
            ) as recipient,
        ):
            recipient.headers["X-CSRF-Token"] = (await recipient.get("/api/v1/auth/csrf")).json()[
                "csrf_token"
            ]
            await new_identity(recipient)
            uid = (await recipient.get("/api/v1/auth/me")).json()["id"]
            data = {
                "recipient_id": uid,
                "report_ids": [rid],
                "hours": 24,
                "reason": "Quarter review",
            }
            assert (await accountant.post(base + "/audit-grants", json=data)).status_code == 404
            created = await owner.post(base + "/audit-grants", json=data)
            assert created.status_code == 201, created.text
            gid = created.json()["id"]
            path = f"/api/v1/audit-access/{gid}/reports/{rid}"
            assert (await recipient.get(base)).status_code == 404
            assert (await recipient.get(base + "/reports/history/" + rid)).status_code == 404
            assert (await owner.get(path)).status_code == 404
            viewed = await recipient.get(path)
            assert viewed.status_code == 200, viewed.text
            assert viewed.json() == report.json()
            assert viewed.headers["cache-control"] == "no-store"
            assert (
                await recipient.get(f"/api/v1/audit-access/{gid}/reports/{uuid4()}")
            ).status_code == 404
            engine = build_engine(settings)
            try:
                with sessionmaker(engine).begin() as db:
                    set_context(db, UUID(uid), UUID(bid))
                    assert list(db.scalars(select(ReportRun.id))) == [UUID(rid)]
            finally:
                engine.dispose()
            invalid = await owner.post(
                base + "/audit-grants", json={**data, "report_ids": [rid, rid]}
            )
            assert invalid.status_code == 422
            revoked = await owner.post(base + f"/audit-grants/{gid}/revoke")
            assert revoked.status_code == 200, revoked.text
            assert (await recipient.get(path)).status_code == 404
            assert (await owner.post(base + f"/audit-grants/{gid}/revoke")).json() == revoked.json()
            audits = (await owner.get(base + "/audit-events", params={"limit": 200})).json()[
                "items"
            ]
            actions = [row["action"] for row in audits]
            assert "audit_access.report_read" in actions
            assert "audit_access.revoked" in actions
            expired_id = uuid4()
            actor = UUID((await owner.get("/api/v1/auth/me")).json()["id"])
            engine = build_engine(settings)
            try:
                with sessionmaker(engine).begin() as db:
                    set_context(db, actor, UUID(bid))
                    db.add(
                        AuditGrant(
                            id=expired_id,
                            business_id=UUID(bid),
                            recipient_id=UUID(uid),
                            granted_by=actor,
                            report_ids=[UUID(rid)],
                            reason="Expired test share",
                            created_at=datetime.now(UTC) - timedelta(hours=2),
                            expires_at=datetime.now(UTC) - timedelta(hours=1),
                        )
                    )
                with sessionmaker(engine).begin() as db:
                    set_context(db, UUID(uid), UUID(bid))
                    assert list(db.scalars(select(ReportRun.id))) == []
            finally:
                engine.dispose()
            assert (
                await recipient.get(f"/api/v1/audit-access/{expired_id}/reports/{rid}")
            ).status_code == 404
