import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from ledgerpilot.config import Settings
from ledgerpilot.provider_integrations import process_stripe_event, signed_event
from ledgerpilot.shared.errors import DomainError


def test_stripe_sandbox_signature_binds_body() -> None:
    payload = b'{"id":"evt_test","type":"invoice.payment_failed","livemode":false}'
    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"test-signing-secret", timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    signature = f"t={timestamp},v1={digest}"
    assert signed_event(payload, signature, "test-signing-secret")["id"] == "evt_test"
    with pytest.raises(DomainError):
        signed_event(payload + b" ", signature, "test-signing-secret")


def test_process_stripe_checkout_completed() -> None:
    db = MagicMock()
    settings = MagicMock(spec=Settings)
    business_id = uuid4()
    event = {
        "id": "evt_checkout",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(business_id),
                "customer": "cus_12345",
                "subscription": "sub_67890",
            }
        },
    }
    db.execute.return_value.scalar_one_or_none.return_value = {"plan": "growth"}
    process_stripe_event(db, settings, event)
    saved = False
    for call in db.execute.call_args_list:
        args, kwargs = call
        if "INSERT INTO app.provider_connections" in str(args[0]):
            params = args[1] if len(args) > 1 else kwargs
            assert params["business"] == business_id
            snapshot = json.loads(params["details"])["snapshot"]
            assert snapshot["paid_access"] is True
            assert snapshot["status"] == "active"
            assert snapshot["plan"] == "growth"
            assert snapshot["subscription_id"] == "sub_67890"
            saved = True
    assert saved, "Expected provider connection to be updated on checkout completion"


def test_process_stripe_past_due_grace_period() -> None:
    db = MagicMock()
    settings = MagicMock(spec=Settings)
    settings.stripe_growth_price_id = "price_growth_123"
    business_id = uuid4()
    event = {
        "id": "evt_past_due",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test",
                "client_reference_id": str(business_id),
                "customer": "cus_12345",
                "status": "past_due",
                "items": {"data": [{"price": {"id": "price_growth_123"}}]},
            }
        },
    }
    db.execute.return_value.scalar_one_or_none.return_value = {"plan": "starter"}
    process_stripe_event(db, settings, event)
    saved = False
    for call in db.execute.call_args_list:
        args, kwargs = call
        if "INSERT INTO app.provider_connections" in str(args[0]):
            params = args[1] if len(args) > 1 else kwargs
            snapshot = json.loads(params["details"])["snapshot"]
            assert snapshot["status"] == "past_due"
            assert snapshot["paid_access"] is True  # Within 3-day grace
            assert snapshot["grace_until"] > time.time()
            assert snapshot["plan"] == "growth"
            saved = True
    assert saved, "Expected past_due subscription to grant grace period"


def test_process_stripe_subscription_deleted() -> None:
    db = MagicMock()
    settings = MagicMock(spec=Settings)
    business_id = uuid4()
    event = {
        "id": "evt_cancel",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test",
                "client_reference_id": str(business_id),
                "customer": "cus_12345",
            }
        },
    }
    db.execute.return_value.scalar_one_or_none.return_value = {
        "plan": "starter",
        "snapshot": {"paid_access": True},
    }
    process_stripe_event(db, settings, event)
    saved = False
    for call in db.execute.call_args_list:
        args, kwargs = call
        if "INSERT INTO app.provider_connections" in str(args[0]):
            params = args[1] if len(args) > 1 else kwargs
            snapshot = json.loads(params["details"])["snapshot"]
            assert snapshot["status"] == "canceled"
            assert snapshot["paid_access"] is False
            saved = True
    assert saved, "Expected deleted subscription to revoke paid access"

