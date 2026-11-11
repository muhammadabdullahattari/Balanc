"""Exercise the local business email path with real PostgreSQL and Mailpit."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from test_phase2_integration import (
    ORIGIN,
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
    mail_token,
    new_identity,
)
from test_phase3_integration import command, invoice, setup

from ledgerpilot.database import build_engine, set_context
from ledgerpilot.local_setup import ROOT, FileSettings, connect, credential_url, read_values
from ledgerpilot.main import create_app
from ledgerpilot.notifications import email_outbox as outbox
from ledgerpilot.notifications import worker

# ruff: noqa: F811 -- shared real-service fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires local PostgreSQL and Mailpit"
)


async def test_workspace_email_delivery_dedupes_and_holds_interrupted_sends(
    client: httpx.AsyncClient, database_settings: FileSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_time = datetime(2026, 11, 10, 12, tzinfo=UTC)
    monkeypatch.setattr(outbox, "now", lambda: review_time)
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        issued = await invoice(api, base, accounts, party)
        assert issued.status_code == 201, issued.text
        recipient = (await api.get("/api/v1/auth/me")).json()
        actor, business = UUID(recipient["id"]), UUID(bid)
        denied = await api.patch(
            base + "/notification-preferences",
            json={"expected_version": 0, "email_reminders": True},
        )
        assert denied.status_code == 403, denied.text
        enabled = database_settings.model_copy(
            update={
                "p2_email_delivery_enabled": True,
                "p2_email_delivery_business_ids": frozenset({business}),
            }
        )
        app = create_app(enabled)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=api.cookies,
                headers=dict(api.headers),
            ) as scoped,
        ):
            preferences = await scoped.get(base + "/notification-preferences")
            assert preferences.json()["email_delivery_available"] is True
            assert preferences.json()["email_reminders"] is False
            saved = await scoped.patch(
                base + "/notification-preferences",
                json={
                    "expected_version": 0,
                    "email_reminders": True,
                    "quiet_start": 0,
                    "quiet_end": 0,
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["email_reminders"] is True
            denied = await scoped.post(
                f"/api/v1/businesses/{uuid4()}/reminders/review",
                json={"as_of": review_time.date().isoformat()},
            )
            assert denied.status_code == 404
            for count in (2, 0):
                reviewed = await scoped.post(
                    base + "/reminders/review", json={"as_of": review_time.date().isoformat()}
                )
                assert reviewed.status_code == 200, reviewed.text
                assert reviewed.json()["email_queued"] == count
                assert reviewed.json()["external_messages_sent"] == 0

            delivered = await asyncio.gather(
                asyncio.to_thread(worker.run_once, enabled, actor, business),
                asyncio.to_thread(worker.run_once, enabled, actor, business),
            )
            assert {item["outcome"] for item in delivered} == {"sent"}
            assert len({item["job_id"] for item in delivered}) == 2
            assert worker.run_once(enabled, actor, business) == {"processed": False}

            review_time += timedelta(days=1)
            reviewed = await scoped.post(
                base + "/reminders/review", json={"as_of": review_time.date().isoformat()}
            )
            assert reviewed.json()["email_queued"] == 2
            engine = build_engine(enabled)
            try:
                sessions = sessionmaker(engine)
                with sessions.begin() as db:
                    claim = worker._claim(db, enabled, business, actor)
                    assert claim is not None
                with sessions.begin() as db:
                    set_context(db, actor, business)
                    assert (
                        db.execute(
                            text(
                                "UPDATE app.business_email_jobs "
                                "SET updated_at=now()-interval '3 min' "
                                "WHERE id=:id AND state='sending'"
                            ),
                            {"id": claim.job_id},
                        ).rowcount
                        == 1
                    )
            finally:
                engine.dispose()

            operations = base + "/email-operations"
            health = await scoped.get(operations)
            assert health.status_code == 200, health.text
            assert health.json()["counts"]["sent"] == 2
            assert "recipient_id" not in health.json()["attention"][0]
            feedback_path = operations + f"/jobs/{delivered[0]['job_id']}/feedback"
            feedback = await scoped.post(feedback_path, json={"kind": "bounced"})
            assert feedback.status_code == 200, feedback.text
            repeated = await scoped.post(feedback_path, json={"kind": "bounced"})
            assert repeated.json() == feedback.json()
            complaint_path = operations + f"/jobs/{delivered[1]['job_id']}/feedback"
            complaint = await scoped.post(complaint_path, json={"kind": "complained"})
            assert complaint.status_code == 200 and complaint.json()["kind"] == "complained"
            assert (await scoped.get(base + "/notification-preferences")).json()["email_suppressed"]
            next_day = review_time + timedelta(days=1)
            monkeypatch.setattr(outbox, "now", lambda: next_day)
            suppressed = await scoped.post(
                base + "/reminders/review", json={"as_of": next_day.date().isoformat()}
            )
            assert suppressed.status_code == 200 and suppressed.json()["email_queued"] == 0
            assert worker.run_once(enabled, actor, business) == {"processed": False}

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                headers={"Origin": ORIGIN},
            ) as reviewer:
                reviewer.headers["X-CSRF-Token"] = (await reviewer.get("/api/v1/auth/csrf")).json()[
                    "csrf_token"
                ]
                address, _, _ = await new_identity(reviewer)
                invitation = await client.post(
                    base + "/invitations", json={"email": address, "role": "reviewer"}
                )
                assert invitation.status_code == 202, invitation.text
                accepted = await reviewer.post(
                    "/api/v1/invitations/accept", json={"token": await mail_token(address)}
                )
                assert accepted.status_code == 200, accepted.text
                reviewer_id = (await reviewer.get("/api/v1/auth/me")).json()["id"]
                assert (await reviewer.get(operations)).status_code == 403
                grant = await scoped.post(
                    operations + "/grants", json={"reviewer_id": reviewer_id, "hours": 1}
                )
                assert grant.status_code == 201, grant.text
                view = await reviewer.get(operations)
                assert view.status_code == 200, view.text
                assert view.json()["counts"]["sent"] == 2
                assert view.json()["grants"] == []
                assert {job["state"] for job in view.json()["attention"]} == {
                    "uncertain",
                    "suppressed",
                }
                assert all("recipient_id" not in job for job in view.json()["attention"])
                assert (
                    await reviewer.post(feedback_path, json={"kind": "complained"})
                ).status_code == 403
                revoked = await scoped.post(operations + f"/grants/{grant.json()['id']}/revoke")
                assert revoked.status_code == 200 and revoked.json()["revoked_at"]
                assert (await reviewer.get(operations)).status_code == 403
                renewed = await scoped.post(
                    operations + "/grants", json={"reviewer_id": reviewer_id, "hours": 1}
                )
                assert renewed.status_code == 201, renewed.text
                values = read_values(ROOT / ".env")
                db_name = make_url(enabled.database_url.get_secret_value()).database
                admin_url = make_url(
                    credential_url(values, "POSTGRES_USER", "POSTGRES_PASSWORD")
                ).set(database=db_name)
                assert db_name and db_name.startswith("ledgerpilot_test_")
                with connect(admin_url) as admin:
                    admin.execute(
                        "UPDATE app.business_email_support_grants "
                        "SET created_at=now()-interval '2 hours', "
                        "expires_at=now()-interval '1 hour' "
                        "WHERE id=%s",
                        (UUID(renewed.json()["id"]),),
                    )
                assert (await reviewer.get(operations)).status_code == 403

            audits = await scoped.get(base + "/audit-events", params={"limit": 200})
            assert audits.status_code == 200, audits.text
            actions = [item["action"] for item in audits.json()["items"]]
            assert actions.count("business_email.feedback_recorded") == 2
            assert "business_email.support_viewed" in actions
            assert "business_email.support_revoked" in actions

            payment = await scoped.post(
                base + "/payments",
                json={
                    **await command(scoped, base),
                    "kind": "receipt",
                    "party_id": party,
                    "accounting_date": "2026-11-01",
                    "bank_account_id": accounts["bank"],
                    "control_account_id": accounts["ar"],
                    "amount": "100",
                    "allocations": [{"document_id": issued.json()["id"], "amount": "100"}],
                },
            )
            assert payment.status_code == 201, payment.text
            assert worker.run_once(enabled, actor, business) == {"processed": False}
            states = {job["job_id"]: job for job in worker.status(enabled, actor, business)}
            history = await scoped.get(base + "/notification-email-history")
            assert history.status_code == 200, history.text
            assert {job["job_id"]: job for job in history.json()["items"]} == states
            other_member = await client.get(base + "/notification-email-history")
            assert other_member.status_code == 200, other_member.text
            assert other_member.json()["items"] == []
            assert states[str(claim.job_id)]["state"] == "uncertain"
            assert any(job["code"] == "ERR_DELIVERY_SUPPRESSED" for job in states.values())
            assert sorted(job["state"] for job in states.values()) == [
                "sent",
                "sent",
                "suppressed",
                "uncertain",
            ]
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8025") as mailbox:
            messages = await mailbox.get(
                "/api/v1/search", params={"query": "to:" + recipient["email"]}
            )
        assert messages.status_code == 200
        subjects = [
            item["Subject"]
            for item in messages.json()["messages"]
            if item["Subject"].startswith("LedgerPilot | ")
        ]
        assert sorted(subjects) == [
            "LedgerPilot | Overdue invoices missing supporting evidence",
            "LedgerPilot | Overdue invoices to review",
        ]
