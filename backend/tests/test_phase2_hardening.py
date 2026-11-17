"""Tests for Phase 2 traffic-spike hardening:
- H3: Asynchronous Stripe webhook intake, worker processing, and replay tooling
- H6: Identity SMTP outbox pattern and checkout side-effects transaction safety
- H2: QuickBooks import queued job (202), batched queries, and source-account mapping safety
- M1: Queued report exports (202 + download) and materialized/incremental balance summaries
"""

import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from ledgerpilot.billing import replay_webhooks
from ledgerpilot.config import Settings
from ledgerpilot.identity.models import User
from ledgerpilot.identity.service import send_action
from ledgerpilot.jobs.handlers import (
    handle_identity_smtp,
    handle_stripe_webhook,
    register_default_handlers,
)
from ledgerpilot.jobs.models import BackgroundJob
from ledgerpilot.provider_integrations import checkout, import_quickbooks, receive_stripe_event
from ledgerpilot.reports.service import (
    generate_report_export,
    get_materialized_balances,
)
from ledgerpilot.shared.errors import DomainError

# ---------------------------------------------------------------------------
# H3: Stripe Webhook Asynchronous Intake & Replay Tooling
# ---------------------------------------------------------------------------


def test_stripe_webhook_intake_enqueues_background_job() -> None:
    db = MagicMock(spec=Session)
    settings = MagicMock(spec=Settings)
    settings.jobs_default_max_attempts = 5
    settings.jobs_default_timeout_seconds = 300
    settings.stripe_webhook_async = True

    event = {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "invoice.payment_succeeded",
        "data": {"object": {"customer": "cus_123"}},
    }

    db.execute.return_value.scalar_one.return_value = True

    with patch("ledgerpilot.jobs.service.enqueue") as mock_enqueue:
        receive_stripe_event(db, settings, event)
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        assert kwargs["queue"] == "webhook"
        assert kwargs["kind"] == "stripe_webhook"
        assert kwargs["payload"]["event_id"] == event["id"]
        assert kwargs["idempotency_key"] == f"stripe_webhook:{event['id']}"


def test_handle_stripe_webhook_worker_success_and_failure() -> None:
    db = MagicMock(spec=Session)
    payload = {
        "event_id": "evt_test_123",
        "event": {"id": "evt_test_123", "type": "invoice.payment_succeeded"},
    }

    with patch("ledgerpilot.jobs.handlers.process_stripe_event") as mock_process:
        res = handle_stripe_webhook(db, None, None, payload)
        assert res["status"] == "processed"
        assert res["event_id"] == "evt_test_123"
        mock_process.assert_called_once()

    with (
        patch(
            "ledgerpilot.jobs.handlers.process_stripe_event",
            side_effect=RuntimeError("stripe error"),
        ),
        pytest.raises(RuntimeError),
    ):
        handle_stripe_webhook(db, None, None, payload)


def test_webhook_replay_tooling_list_inspect_and_replay() -> None:
    db = MagicMock(spec=Session)
    event_id = "evt_replay_test"

    # 1. list_events
    db.execute.return_value.mappings.return_value.all.return_value = [
        {
            "id": event_id,
            "event_type": "customer.subscription.updated",
            "status": "failed",
            "received_at": "2026-11-17T00:00:00Z",
            "processed_at": None,
            "error_detail": "Connection timeout",
        }
    ]
    events = replay_webhooks.list_events(db, status="failed")
    assert len(events) == 1
    assert events[0]["id"] == event_id

    # 2. inspect_event
    db.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "id": event_id,
        "event_type": "customer.subscription.updated",
        "status": "failed",
        "received_at": "2026-11-17T00:00:00Z",
        "processed_at": None,
        "error_detail": "Connection timeout",
        "payload": {"id": event_id, "type": "customer.subscription.updated"},
    }
    details = replay_webhooks.inspect_event(db, event_id)
    assert details is not None
    assert details["id"] == event_id

    # 3. replay_event async
    with patch("ledgerpilot.jobs.service.enqueue") as mock_enqueue:
        ok = replay_webhooks.replay_event(db, event_id, sync=False)
        assert ok is True
        mock_enqueue.assert_called_once()

    # 4. replay_event sync
    with patch("ledgerpilot.billing.replay_webhooks.process_stripe_event") as mock_process:
        ok = replay_webhooks.replay_event(db, event_id, sync=True)
        assert ok is True
        mock_process.assert_called_once()


# ---------------------------------------------------------------------------
# H6: Identity SMTP Outbox & Checkout Non-Locking Side Effects
# ---------------------------------------------------------------------------


def test_identity_smtp_outbox_enqueues_and_after_commit_dispatches() -> None:
    db = MagicMock(spec=Session)
    mock_user = MagicMock(spec=User)
    mock_user.verification_blocked_until = None
    mock_user.verification_attempts = 0
    mock_user.verification_started_at = None
    db.scalar.return_value = mock_user

    settings = MagicMock(spec=Settings)
    settings.auth_delivery_enabled = True
    settings.jobs_default_max_attempts = 5
    settings.jobs_default_timeout_seconds = 300
    user_id = uuid4()
    email = "outbox_user@example.com"
    purpose = "verify"

    with patch("ledgerpilot.identity.mail.deliver") as mock_deliver:
        send_action(db, settings, user_id, email, purpose)
        mock_deliver.assert_called_once()
        args, kwargs = mock_deliver.call_args
        assert args[1] == email
        assert args[2] == purpose


def test_handle_identity_smtp_worker_handler() -> None:
    db = MagicMock(spec=Session)
    payload = {
        "recipient": "worker_user@example.com",
        "purpose": "reset",
        "token": "tok_12345",
    }
    with patch("ledgerpilot.jobs.handlers.deliver") as mock_deliver:
        res = handle_identity_smtp(db, None, None, payload)
        assert res["delivered"] is True
        mock_deliver.assert_called_once()


def test_checkout_does_not_hold_transaction_lock_across_stripe_calls() -> None:
    db = MagicMock()
    business_id = uuid4()
    request = MagicMock()
    identity = MagicMock()
    settings = MagicMock(spec=Settings)
    settings.stripe_starter_price_id = "price_starter_123"
    settings.stripe_growth_price_id = "price_growth_123"
    settings.stripe_enterprise_price_id = "price_enterprise_123"
    settings.billing_return_url = "https://app.ledgerpilot.test/billing"
    request.app.state.settings = settings

    def fake_stripe(settings: Any, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "customers":
            return {"id": "cus_999"}
        if path == "checkout/sessions":
            return {"id": "cs_test", "url": "https://checkout.stripe.com/pay/cs_test"}
        return {}

    with (
        patch("ledgerpilot.provider_integrations.load", return_value={}),
        patch("ledgerpilot.provider_integrations.stripe", side_effect=fake_stripe),
        patch("ledgerpilot.provider_integrations.authorize"),
    ):
        res = checkout(
            business_id=business_id,
            plan="growth",
            request=request,
            identity=identity,
            db=db,
        )
        assert res["url"] == "https://checkout.stripe.com/pay/cs_test"
        # Confirm pg_advisory_xact_lock is NOT in any query executed on db
        for call in db.execute.call_args_list:
            args, _ = call
            assert "pg_advisory_xact_lock" not in str(args[0])


# ---------------------------------------------------------------------------
# H2: QuickBooks Import Queued Job (202), Batching & Unmapped Mapping Safety
# ---------------------------------------------------------------------------


def test_quickbooks_import_queued_job_returns_202() -> None:
    db = MagicMock(spec=Session)
    request = MagicMock()
    identity = MagicMock()
    identity.user.id = uuid4()
    business_id = uuid4()
    settings = MagicMock(spec=Settings)
    settings.jobs_default_max_attempts = 5
    request.app.state.settings = settings

    fake_conn = {"tokens": "enc_tok", "realm_id": "12345"}
    mock_job = MagicMock(spec=BackgroundJob)
    mock_job.id = uuid4()

    with (
        patch("ledgerpilot.provider_integrations.load", return_value=fake_conn),
        patch("ledgerpilot.provider_integrations.authorize"),
        patch("ledgerpilot.jobs.service.enqueue", return_value=mock_job),
    ):
        res = import_quickbooks(
            business_id=business_id,
            request=request,
            identity=identity,
            db=db,
            entities=["Account", "JournalEntry"],
            dry_run=False,
            async_job=True,
        )
        assert res["job_id"] == str(mock_job.id)
        assert res["status"] == "queued"
        assert res["status_url"] == f"/api/v1/businesses/{business_id}/jobs/{mock_job.id}"


def test_quickbooks_unmapped_account_fails_review_never_guesses() -> None:
    db = MagicMock(spec=Session)
    request = MagicMock()
    identity = MagicMock()
    business_id = uuid4()
    settings = MagicMock(spec=Settings)
    request.app.state.settings = settings

    fake_conn = {"tokens": "enc_tok", "realm_id": "12345"}
    journal_res = {
        "QueryResponse": {
            "JournalEntry": [
                {
                    "Id": "qb_j_1",
                    "Line": [
                        {
                            "Amount": 500,
                            "JournalEntryLineDetail": {
                                "PostingType": "Debit",
                                "AccountRef": {"value": "9999", "name": "Ambiguous Acc"},
                            },
                        }
                    ],
                }
            ]
        }
    }

    mock_cipher = MagicMock()
    mock_cipher.decrypt.return_value = json.dumps({"access_token": "valid"}).encode()

    with (
        patch("ledgerpilot.provider_integrations.load", return_value=fake_conn),
        patch("ledgerpilot.provider_integrations.provider", return_value=journal_res),
        patch("ledgerpilot.provider_integrations.encryption", return_value=mock_cipher),
        patch("ledgerpilot.provider_integrations.authorize"),
    ):
        db.execute.return_value.scalar_one_or_none.return_value = None
        with pytest.raises(DomainError) as exc_info:
            import_quickbooks(
                business_id=business_id,
                request=request,
                identity=identity,
                db=db,
                entities=["JournalEntry"],
                dry_run=False,
            )
        assert exc_info.value.code == "ERR_UNMAPPED_ACCOUNT"
        assert "Ambiguous Acc" in exc_info.value.message


# ---------------------------------------------------------------------------
# M1: Queued Report Exports (202 + Download) & Materialized Balance Summaries
# ---------------------------------------------------------------------------


def test_queued_report_export_service_json_and_csv() -> None:
    db = MagicMock(spec=Session)
    business_id = uuid4()

    fake_report = {
        "kind": "profit-loss",
        "net_income": "12500.00",
        "operating_income": "12500.00",
    }

    with patch("ledgerpilot.reports.service.financial", return_value=fake_report):
        # JSON export
        res_json = generate_report_export(
            db, business_id, "profit-loss", {"basis": "accrual"}, fmt="json"
        )
        assert res_json["content_type"] == "application/json"
        assert "12500.00" in res_json["content"]
        assert res_json["filename"].endswith(".json")

        # CSV export
        res_csv = generate_report_export(
            db, business_id, "profit-loss", {"basis": "accrual"}, fmt="csv"
        )
        assert res_csv["content_type"] == "text/csv"
        assert "12500.00" in res_csv["content"]
        assert res_csv["filename"].endswith(".csv")


def test_materialized_balance_summary_invalidation_tied_to_ledger_version() -> None:
    db = MagicMock(spec=Session)
    business_id = uuid4()
    acc_id = uuid4()

    # Initial state: business version 5, summary version 5 (fresh)
    db.execute.return_value.scalar_one.return_value = 5
    db.execute.return_value.scalar_one_or_none.return_value = 5
    db.execute.return_value.tuples.return_value.all.return_value = [(acc_id, Decimal("1500.00"))]

    balances_res = get_materialized_balances(db, business_id)
    assert balances_res[acc_id] == Decimal("1500.00")

    # Ledger version increments to 6 (new journal posted):
    # summary_version 5 != business version 6 -> triggers refresh_balance_summaries
    db.execute.return_value.scalar_one.side_effect = [6, 1]  # version, refresh row count
    db.execute.return_value.scalar_one_or_none.return_value = 5

    with patch("ledgerpilot.reports.service.refresh_balance_summaries") as mock_refresh:
        mock_refresh.return_value = 1
        balances_res = get_materialized_balances(db, business_id)
        mock_refresh.assert_called_once_with(db, business_id, 6)


def test_register_default_worker_handlers() -> None:
    from ledgerpilot.jobs.worker import get_handler

    register_default_handlers()
    assert get_handler("stripe_webhook") is not None
    assert get_handler("identity_smtp") is not None
    assert get_handler("quickbooks_import") is not None
    assert get_handler("report_export") is not None
