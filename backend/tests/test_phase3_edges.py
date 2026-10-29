"""Real expiry, quotas and queue replay with isolated operator-controlled clock fixtures."""

import asyncio
import os
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_closure import CREATED_FILES, cleanup_originals  # noqa: F401
from test_phase3_integration import setup

from ledgerpilot.database import set_context
from ledgerpilot.documents.worker import run_once
from ledgerpilot.local_setup import ROOT, FileSettings, read_values

# ruff: noqa: F811 -- imported real fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires real PostgreSQL/SMTP/ClamAV"
)


@contextmanager
def operator(settings: FileSettings, actor: str, bid: str):
    private = read_values(ROOT / ".env")
    runtime = make_url(settings.database_url.get_secret_value())
    assert runtime.database and runtime.database.startswith("ledgerpilot_test_")
    url = runtime.set(
        username=private["MIGRATION_DB_USER"], password=private["MIGRATION_DB_PASSWORD"]
    )
    engine = create_engine(url, hide_parameters=True)
    try:
        with Session(engine) as db, db.begin():
            set_context(db, UUID(actor), UUID(bid))
            yield db
    finally:
        engine.dispose()


async def test_trial_expiry_retains_export_privacy_and_quota_limits_apply(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        with operator(database_settings, uid, bid) as db:
            db.execute(
                text(
                    "UPDATE app.trial_entitlements SET expires_at=now()-interval '1 hour' "
                    "WHERE business_id=:b"
                ),
                {"b": bid},
            )
        assert not (await api.get(base + "/entitlement")).json()["write_allowed"]
        assert (
            await api.post(base + "/parties", json={"name": "Denied write", "roles": ["customer"]})
        ).status_code == 403
        assert (
            await api.get(base + "/trial-balance", params={"as_of": "2026-10-31"})
        ).status_code == 200
        payload = {
            "idempotency_key": uuid4().hex,
            "format": "journals_csv",
            "start": "2026-10-01",
            "end": "2026-10-31",
        }
        exported = await api.post(base + "/exports/snapshots", json=payload)
        assert exported.status_code == 201, exported.text
        privacy_request = await api.post(
            base + "/privacy-requests", json={"kind": "export", "reason": "Retention access"}
        )
        assert privacy_request.status_code == 201
        reviewed = await client.post(
            base + f"/privacy-requests/{privacy_request.json()['id']}/reviews",
            json={
                "outcome": "export_ready",
                "reason": "Independent retained-access review",
                "policy_reference": "Local fixture privacy policy",
            },
        )
        assert reviewed.status_code == 201, reviewed.text
        own_export = await api.get(base + "/privacy-export")
        assert own_export.status_code == 200, own_export.text
        assert own_export.json()["privacy_requests"][0]["status"] == "export_ready"
        with operator(database_settings, uid, bid) as db:
            db.execute(
                text(
                    "UPDATE app.trial_entitlements SET expires_at=NULL,import_row_limit=1,"
                    "document_byte_limit=8 WHERE business_id=:b"
                ),
                {"b": bid},
            )
        bank = {
            "account_id": a["bank"],
            "content": "date,description,amount\n2026-10-01,A,1\n2026-10-02,B,2\n",
            "mapping": {
                "date_column": "date",
                "description_column": "description",
                "amount_column": "amount",
            },
        }
        assert (await api.post(base + "/bank-imports/preview", json=bank)).status_code == 413
        upload = await api.post(
            base + "/evidence/upload",
            params={"filename": "large.csv"},
            content=b"date,total\n2026-10-01,100\n",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 413, upload.text
        assert (await api.get(base + "/usage")).json()["documents"] == 0
        for _ in range(2):
            result = await api.post(
                base + "/exports/snapshots", json={**payload, "idempotency_key": uuid4().hex}
            )
            assert result.status_code == 201, result.text
        capped = await api.post(
            base + "/exports/snapshots", json={**payload, "idempotency_key": uuid4().hex}
        )
        assert capped.status_code == 413, capped.text


async def test_failed_quarantined_job_replay_rescans_and_never_replays_completed_jobs(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    from pathlib import Path

    async with accountant_context(client, database_settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        uploaded = await api.post(
            base + "/evidence/upload",
            params={"filename": "review.csv"},
            content=b"field,value\nA,1\n",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 201, uploaded.text
        eid, jid = uploaded.json()["id"], uploaded.json()["job_id"]
        CREATED_FILES.append(Path(database_settings.evidence_storage_root) / bid / (eid + ".bin"))
        unavailable = database_settings.model_copy(update={"clamav_port": 1})
        for _ in range(3):
            with operator(database_settings, uid, bid) as db:
                db.execute(
                    text(
                        "UPDATE app.evidence_jobs SET available_at=now() "
                        "WHERE business_id=:b AND id=:id"
                    ),
                    {"b": bid, "id": jid},
                )
            result = await asyncio.to_thread(run_once, unavailable, UUID(uid), UUID(bid))
            assert (
                result["completion_accepted"] and result["error_code"] == "ERR_SCAN_UNAVAILABLE"
            ), result
        jobs = (await api.get(base + "/jobs")).json()["items"]
        assert jobs[0]["state"] == "failed" and jobs[0]["attempts"] == 3
        replay = await api.post(base + f"/jobs/{jid}/replay", json={"reason": "Scanner restored"})
        assert replay.status_code == 200, replay.text
        result = await asyncio.to_thread(run_once, database_settings, UUID(uid), UUID(bid))
        assert result["completion_accepted"] and result["outcome"] == "unsupported", result
        assert (
            await api.post(
                base + f"/jobs/{jid}/replay", json={"reason": "Must not repeat completed work"}
            )
        ).status_code == 409
