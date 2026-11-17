"""Exercise the independent cleanup approval and operator confirmation workflow."""

import os
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401

from ledgerpilot.database import set_context
from ledgerpilot.documents import storage
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.workspace.models import Evidence

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
)


async def test_backup_cleanup_requires_independent_approval_and_confirmation(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        settings = database_settings.model_copy(
            update={
                "p2_retention_business_ids": frozenset({UUID(bid)}),
                "p2_payload_disposal_enabled": True,
            }
        )
        app = create_app(settings)
        base = f"/api/v1/businesses/{bid}/retention"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=client.cookies,
                headers=dict(client.headers),
            ) as owner,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as requester,
        ):
            policy = await requester.post(
                base + "/cleanup/policies",
                json={
                    "record_type": "backup_copy",
                    "location": "operator_backup",
                    "retention_days": 30,
                    "approval_role": "owner",
                    "reference": "Synthetic policy",
                },
            )
            assert policy.status_code == 201, policy.text
            pid = policy.json()["id"]
            approve_policy = base + f"/cleanup/policies/{pid}/approve"
            assert (await requester.post(approve_policy)).status_code == 403
            assert (await owner.post(approve_policy)).status_code == 200
            payload = {
                "policy_id": pid,
                "resource_id": str(uuid4()),
                "record_date": "2020-01-01",
                "reference": "Synthetic inventory copy",
            }
            created = await requester.post(base + "/cleanup/requests", json=payload)
            assert created.status_code == 201, created.text
            rid = created.json()["id"]
            job = base + f"/cleanup/requests/{rid}"
            assert (await requester.post(base + "/cleanup/requests", json=payload)).json()[
                "id"
            ] == rid
            assert (await owner.post(job + "/approve")).status_code == 409
            assert (await requester.post(job + "/preview")).json()["eligible"]
            assert (await owner.post(job + "/approve")).status_code == 200
            hold = await owner.post(
                base + "/holds",
                json={
                    "category": "all",
                    "kind": "regulatory",
                    "reason": "Synthetic hold",
                },
            )
            assert hold.status_code == 201, hold.text
            assert (await owner.post(job + "/execute")).status_code == 409
            hid = hold.json()["id"]
            assert (
                await owner.post(base + f"/holds/{hid}/release", json={"reason": "Test release"})
            ).status_code == 200
            pending = await owner.post(job + "/execute")
            assert pending.status_code == 200, pending.text
            assert pending.json()["status"] == "awaiting_confirmation"
            assert (await owner.post(job + "/execute")).json()["attempts"] == 1
            evidence = {"evidence_reference": "Synthetic operator receipt"}
            assert (await requester.post(job + "/confirm", json=evidence)).status_code == 403
            assert (await owner.post(job + "/confirm", json=evidence)).json()[
                "status"
            ] == "confirmed"
            history = (await owner.get(job + "/events")).json()
            assert {event["action"] for event in history} == {
                "requested",
                "previewed",
                "approved",
                "awaiting_confirmation",
                "confirmed",
            }


async def test_upload_deletion_failure_and_retry_preserves_accounting_record(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        business_id = UUID(bid)
        actor = UUID((await accountant.get("/api/v1/auth/me")).json()["id"])
        settings = database_settings.model_copy(
            update={
                "p2_retention_business_ids": frozenset({business_id}),
                "p2_payload_disposal_enabled": True,
            }
        )
        app = create_app(settings)
        base = f"/api/v1/businesses/{bid}/retention"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=client.cookies,
                headers=dict(client.headers),
            ) as owner,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=accountant.cookies,
                headers=dict(accountant.headers),
            ) as requester,
        ):
            # 1. Stage an encrypted evidence file and its database record
            evidence_id = uuid4()
            payload = b"Tax-invoice-2020-01-01-encrypted-payload"
            object_key, content_hash = storage.put(settings, business_id, evidence_id, payload)

            target_file = storage.path(settings, object_key)
            assert target_file.is_file(), "Encrypted payload must exist on disk"

            with app.state.sessions.begin() as db:
                set_context(db, actor, business_id)
                evidence_row = Evidence(
                    id=evidence_id,
                    business_id=business_id,
                    filename="tax_invoice_2020.pdf",
                    content_hash=content_hash,
                    media_type="application/pdf",
                    size=len(payload),
                    status="extracted",
                    scan_version="ClamAV 1.0.0",
                    object_key=object_key,
                    raw_text="INVOICE #2020-001 TOTAL $1,250.00",
                    extraction={"invoice_number": "2020-001", "total": "1250.00"},
                    created_by=actor,
                )
                db.add(evidence_row)
                db.flush()

            # 2. Create and approve the cleanup policy for document_payload
            policy_res = await requester.post(
                base + "/cleanup/policies",
                json={
                    "record_type": "document_payload",
                    "location": "local_evidence",
                    "retention_days": 30,
                    "approval_role": "owner",
                    "reference": "Statutory document disposal policy",
                },
            )
            assert policy_res.status_code == 201, policy_res.text
            pid = policy_res.json()["id"]

            approve_res = await owner.post(base + f"/cleanup/policies/{pid}/approve")
            assert approve_res.status_code == 200, approve_res.text

            # 3. Create cleanup request with elapsed record_date
            req_payload = {
                "policy_id": pid,
                "resource_id": str(evidence_id),
                "record_date": "2020-01-01",
                "reference": "Disposal of expired 2020 invoice payload",
            }
            create_req_res = await requester.post(base + "/cleanup/requests", json=req_payload)
            assert create_req_res.status_code == 201, create_req_res.text
            rid = create_req_res.json()["id"]
            job_url = base + f"/cleanup/requests/{rid}"

            # 4. Dry run preview and independent approval
            preview_res = await requester.post(job_url + "/preview")
            assert preview_res.status_code == 200, preview_res.text
            assert preview_res.json()["eligible"] is True

            approve_job_res = await owner.post(job_url + "/approve")
            assert approve_job_res.status_code == 200, approve_job_res.text
            assert approve_job_res.json()["status"] == "approved"

            # 5. Simulate storage failure on first execution attempt
            real_unlink = Path.unlink
            failure_simulated = True

            def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
                nonlocal failure_simulated
                if failure_simulated and str(self) == str(target_file):
                    raise OSError("Simulated I/O failure on encrypted storage disk")
                real_unlink(self, *args, **kwargs)

            with patch.object(Path, "unlink", side_effect=failing_unlink, autospec=True):
                fail_exec = await owner.post(job_url + "/execute")
                assert fail_exec.status_code == 200, fail_exec.text
                fail_data = fail_exec.json()
                assert fail_data["status"] == "failed"
                assert fail_data["attempts"] == 1
                assert "Storage operation failed" in fail_data["outcome"]

            # Verify file STILL exists on disk after failed attempt
            assert target_file.is_file(), "File must not be deleted when storage fails"

            # Verify accounting metadata in database is intact
            with app.state.sessions.begin() as db:
                set_context(db, actor, business_id)
                ev = db.scalar(select(Evidence).where(Evidence.id == evidence_id))
                assert ev is not None, "Accounting metadata row must be preserved"
                assert ev.raw_text == "INVOICE #2020-001 TOTAL $1,250.00"
                assert ev.filename == "tax_invoice_2020.pdf"

            # 6. Retry execution without the failure (storage is healthy now)
            failure_simulated = False
            retry_exec = await owner.post(job_url + "/execute")
            assert retry_exec.status_code == 200, retry_exec.text
            retry_data = retry_exec.json()
            assert retry_data["status"] == "verified"
            assert retry_data["attempts"] == 2
            assert "Managed payload absent; accounting metadata retained." in retry_data["outcome"]

            # Verify file is now deleted from storage
            assert not target_file.is_file(), "File must be removed after successful retry"

            # Verify accounting record is STILL preserved in the database
            with app.state.sessions.begin() as db:
                set_context(db, actor, business_id)
                ev_after = db.scalar(select(Evidence).where(Evidence.id == evidence_id))
                assert ev_after is not None, "Accounting record must remain in database"
                assert ev_after.extraction["invoice_number"] == "2020-001"
                assert ev_after.content_hash == content_hash

            # 7. Verify both attempts appear in the audit history
            history_res = await owner.get(job_url + "/events")
            assert history_res.status_code == 200, history_res.text
            events = history_res.json()
            actions = [e["action"] for e in events]

            # Audit events are sorted desc by created_at
            assert "failed" in actions, "Failure attempt must be recorded in audit history"
            assert "verified" in actions, "Retry attempt must be recorded in audit history"

            failed_event = next(e for e in events if e["action"] == "failed")
            verified_event = next(e for e in events if e["action"] == "verified")

            assert "Storage operation failed" in failed_event["detail"]["reason"]
            assert "Managed payload absent" in verified_event["detail"]["reason"]
