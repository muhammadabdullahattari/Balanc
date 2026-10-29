"""Remaining P1 workflows on real tenant sessions, PostgreSQL and scanning."""

import asyncio
import hashlib
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import command, invoice, setup

from ledgerpilot.documents.worker import run_once
from ledgerpilot.local_setup import FileSettings

# ruff: noqa: F811 -- real imported pytest fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires real PostgreSQL/SMTP/ClamAV"
)

CREATED_FILES: list[Path] = []


@pytest.fixture(autouse=True)
def cleanup_originals():
    yield
    for path in CREATED_FILES:
        path.unlink(missing_ok=True)
    CREATED_FILES.clear()


async def clean_upload(
    api: httpx.AsyncClient,
    base: str,
    bid: str,
    settings: FileSettings,
    text: bytes = b"supplier,date,total\nACME,2026-10-01,100\n",
) -> str:
    upload = await api.post(
        base + "/evidence/upload",
        params={"filename": "source.csv"},
        content=text,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 201, upload.text
    CREATED_FILES.append(
        Path(settings.evidence_storage_root) / bid / (upload.json()["id"] + ".bin")
    )
    uid = (await api.get("/api/v1/auth/me")).json()["id"]
    outcome = await asyncio.to_thread(run_once, settings, UUID(uid), UUID(bid))
    assert outcome["completion_accepted"] and outcome["outcome"] in {"unsupported", "extracted"}, (
        outcome
    )
    return str(upload.json()["id"])


async def test_reviewed_duplicate_identity_and_correction_rule_suggestions(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        one = await clean_upload(api, base, bid, database_settings)
        two = await clean_upload(api, base, bid, database_settings, b"different,bytes\n1,2\n")
        for eid, supplier in [(one, "ACME, Ltd."), (two, " acme ltd ")]:
            result = await api.post(
                base + f"/evidence/{eid}/identity",
                json={
                    "expected_version": 0,
                    "supplier": supplier,
                    "document_date": "2026-10-01",
                    "currency": "USD",
                    "total": "100",
                    "reason": "Reviewed original source",
                },
            )
            assert result.status_code == 201, result.text
        candidates = await api.get(base + f"/evidence/{one}/duplicate-candidates")
        assert candidates.status_code == 200, candidates.text
        assert candidates.json()["items"] == [
            {"evidence_id": two, "reasons": ["reviewed_supplier_date_total"]}
        ]
        assert not candidates.json()["automatic_merge"]
        bank = {
            "account_id": a["bank"],
            "content": (
                'date,description,amount\n2026-10-01,"ACME, Ltd.",-10\n2026-10-01,acme ltd,-20\n'
            ),
            "mapping": {
                "date_column": "date",
                "description_column": "description",
                "amount_column": "amount",
            },
        }
        preview = await api.post(base + "/bank-imports/preview", json=bank)
        imported = await api.post(
            base + "/bank-imports", json={**bank, "expected_hash": preview.json()["file_hash"]}
        )
        assert imported.status_code == 201, imported.text
        rows = (await api.get(base + "/bank-transactions")).json()["items"]
        for row in rows:
            amount = str(abs(Decimal(row["amount"])))
            classified = await api.post(
                base + f"/bank-transactions/{row['id']}/disposition",
                json={
                    **await command(api, base),
                    "expected_row_version": 0,
                    "action": "categorize",
                    "reason": "Initial review",
                    "splits": [{"account_id": a["cost"], "amount": amount}],
                },
            )
            assert classified.status_code == 200, classified.text
            corrected = await api.post(
                base + f"/bank-transactions/{row['id']}/reclassifications",
                json={
                    **await command(api, base),
                    "accounting_date": "2026-10-02",
                    "reason": "Reviewed correct category",
                    "splits": [{"account_id": a["interest"], "amount": amount}],
                },
            )
            assert corrected.status_code == 201, corrected.text
        proposals = await api.get(base + "/bank-rules/correction-suggestions")
        assert proposals.status_code == 200, proposals.text
        assert proposals.json()["items"][0]["sample_count"] == 2
        assert not proposals.json()["automatic_activation"]


async def test_retained_export_resumes_same_bytes_and_privacy_review_is_independent(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        assert (await invoice(api, base, a, party)).status_code == 201
        payload = {
            "idempotency_key": uuid4().hex,
            "format": "journals_csv",
            "start": "2026-10-01",
            "end": "2026-10-31",
        }
        snapshot = await api.post(base + "/exports/snapshots", json=payload)
        assert snapshot.status_code == 201, snapshot.text
        data = snapshot.json()
        assert (await api.post(base + "/exports/snapshots", json=payload)).json() == data
        path = base + f"/exports/snapshots/{data['id']}/download"
        first = await api.get(path, headers={"Range": "bytes=0-19"})
        assert first.status_code == 206, first.text
        assert (await invoice(api, base, a, party)).status_code == 201
        second = await api.get(
            path,
            headers={"Range": f"bytes=20-{data['size'] - 1}", "If-Match": first.headers["etag"]},
        )
        assert second.status_code == 206, second.text
        assert hashlib.sha256(first.content + second.content).hexdigest() == data["sha256"]
        assert (await client.get(path)).status_code == 404
        assert (await api.get(path, headers={"Range": "bytes=999999-9999999"})).status_code == 416
        assert (await api.get(path, headers={"If-Match": '"wrong"'})).status_code == 412
        from sqlalchemy import text
        from test_phase3_edges import operator

        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        expired_id = str(uuid4())
        with operator(database_settings, uid, bid) as db:
            db.execute(
                text("""INSERT INTO app.export_snapshots(
             id,business_id,key,request_hash,format,created_by,
             business_version,digest,size,payload,expires_at)
             SELECT :id,business_id,:key,request_hash,format,created_by,
             business_version,digest,size,payload,
             now()-interval '1 hour' FROM app.export_snapshots WHERE id=:source AND business_id=:b
            """),
                {"id": expired_id, "key": uuid4().hex, "source": data["id"], "b": bid},
            )
        assert (await api.get(base + f"/exports/snapshots/{expired_id}")).status_code == 410
        assert (
            await api.post(
                base + "/exports/snapshots", json={**payload, "idempotency_key": uuid4().hex}
            )
        ).status_code == 201
        assert (await api.get(base + f"/exports/snapshots/{expired_id}")).status_code == 404
        request = await client.post(
            base + "/privacy-requests", json={"kind": "export", "reason": "Own data copy"}
        )
        assert request.status_code == 201, request.text
        rid = request.json()["id"]
        review = {
            "outcome": "export_ready",
            "reason": "Verified scoped export path",
            "policy_reference": "Local test policy v1",
        }
        assert (
            await client.post(base + f"/privacy-requests/{rid}/reviews", json=review)
        ).status_code == 403
        decision = await api.post(base + f"/privacy-requests/{rid}/reviews", json=review)
        assert decision.status_code == 201 and not decision.json()["deleted"], decision.text
        history = await client.get(base + f"/privacy-requests/{rid}")
        assert history.status_code == 200 and history.json()["status"] == "export_ready", (
            history.text
        )
