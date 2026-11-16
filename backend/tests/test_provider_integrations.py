import hashlib
import hmac
import json
import time
from datetime import UTC
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


def test_authorize_dynamic_grace_period_enforcement() -> None:
    from datetime import datetime

    from ledgerpilot.shared.errors import DomainError
    from ledgerpilot.tenancy.models import Membership
    from ledgerpilot.tenancy.service import authorize

    business_id = uuid4()
    user_id = uuid4()

    membership = MagicMock(spec=Membership)
    membership.role = "member"
    membership.active = True

    identity = MagicMock()
    identity.user.id = user_id
    identity.family.mfa = False

    trial = MagicMock()
    trial.status = "trial_ended"
    trial.expires_at = datetime(2026, 1, 1, tzinfo=UTC)

    # Case 1: Active grace period (grace_until in future)
    db = MagicMock()
    db.info = {
        "http_method": "POST",
        "request_path": f"/businesses/{business_id}/journals",
        "trial_scope": business_id,
    }
    db.scalar.side_effect = [membership, trial]
    future_grace = time.time() + 86400
    db.execute.return_value.scalar_one_or_none.return_value = {
        "snapshot": {"status": "past_due", "grace_until": future_grace, "paid_access": True}
    }
    # Should succeed because grace is still active
    auth_mem = authorize(db, identity, business_id)
    assert auth_mem == membership

    # Case 2: Expired grace period (grace_until in past)
    db = MagicMock()
    db.info = {
        "http_method": "POST",
        "request_path": f"/businesses/{business_id}/journals",
        "trial_scope": business_id,
    }
    db.scalar.side_effect = [membership, trial]
    past_grace = time.time() - 3600
    db.execute.return_value.scalar_one_or_none.return_value = {
        "snapshot": {"status": "past_due", "grace_until": past_grace, "paid_access": True}
    }
    with pytest.raises(DomainError) as exc_info:
        authorize(db, identity, business_id)
    assert exc_info.value.code == "ERR_ENTITLEMENT"


def test_quickbooks_unmapped_account_rejection() -> None:
    from unittest.mock import patch

    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from ledgerpilot.provider_integrations import import_quickbooks

    db = MagicMock()
    business_id = uuid4()
    identity = MagicMock()
    request = MagicMock()

    key = Fernet.generate_key()
    cipher = Fernet(key)
    tokens_json = json.dumps({"access_token": "qb_token_test", "refresh_token": "qb_refresh"})
    encrypted_tokens = cipher.encrypt(tokens_json.encode()).decode()

    settings = MagicMock(spec=Settings)
    settings.token_encryption_key = key.decode()
    settings.mfa_encryption_key = SecretStr(key.decode())
    request.app.state.settings = settings

    qb_connection = {
        "tokens": encrypted_tokens,
        "realm_id": "123456",
    }

    journal_response = {
        "QueryResponse": {
            "JournalEntry": [
                {
                    "Id": "999",
                    "TxnDate": "2026-11-16",
                    "Line": [
                        {
                            "Amount": 100,
                            "JournalEntryLineDetail": {
                                "PostingType": "Debit",
                                "AccountRef": {"value": "9999", "name": "Unknown Acc"},
                            },
                        }
                    ],
                }
            ]
        }
    }

    with patch("ledgerpilot.provider_integrations.load", return_value=qb_connection), \
         patch("ledgerpilot.provider_integrations.provider", return_value=journal_response), \
         patch("ledgerpilot.provider_integrations.authorize"):

        # 1. Live import mode: Account not found in app.accounts must raise ERR_UNMAPPED_ACCOUNT
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
        assert "Unknown Acc" in exc_info.value.message

        # 2. Dry run mode: Must collect unmapped account without raising, and reconciled is False
        db.execute.return_value.scalar_one_or_none.return_value = None
        summary = import_quickbooks(
            business_id=business_id,
            request=request,
            identity=identity,
            db=db,
            entities=["JournalEntry"],
            dry_run=True,
        )
        assert summary["status"] == "dry_run"
        assert len(summary["unmapped_accounts"]) == 1
        assert summary["unmapped_accounts"][0]["name"] == "Unknown Acc"
        assert summary["reconciled"] is False


def test_quickbooks_customer_and_vendor_import() -> None:
    from typing import Any
    from unittest.mock import patch

    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from ledgerpilot.provider_integrations import import_quickbooks

    db = MagicMock()
    business_id = uuid4()
    identity = MagicMock()
    request = MagicMock()

    key = Fernet.generate_key()
    cipher = Fernet(key)
    tokens_json = json.dumps({"access_token": "qb_token_test", "refresh_token": "qb_refresh"})
    encrypted_tokens = cipher.encrypt(tokens_json.encode()).decode()

    settings = MagicMock(spec=Settings)
    settings.token_encryption_key = key.decode()
    settings.mfa_encryption_key = SecretStr(key.decode())
    request.app.state.settings = settings

    qb_connection = {
        "tokens": encrypted_tokens,
        "realm_id": "123456",
    }

    def fake_provider(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        query = kwargs.get("params", {}).get("query", "")
        if "Customer" in query:
            return {
                "QueryResponse": {
                    "Customer": [
                        {
                            "Id": "c1",
                            "DisplayName": "Acme Client LLC",
                            "PrimaryEmailAddr": {"Address": "billing@acmeclient.com"},
                        }
                    ]
                }
            }
        if "Vendor" in query:
            return {
                "QueryResponse": {
                    "Vendor": [
                        {
                            "Id": "v1",
                            "DisplayName": "Office Depot Supplies",
                            "PrimaryEmailAddr": {"Address": "invoicing@officedepot.com"},
                        }
                    ]
                }
            }
        return {"QueryResponse": {}}

    with patch("ledgerpilot.provider_integrations.load", return_value=qb_connection), \
         patch("ledgerpilot.provider_integrations.provider", side_effect=fake_provider), \
         patch("ledgerpilot.provider_integrations.authorize"):

        db.execute.return_value.scalar_one_or_none.return_value = None
        res = import_quickbooks(
            business_id=business_id,
            request=request,
            identity=identity,
            db=db,
            entities=["Customer", "Vendor"],
            dry_run=False,
        )
        assert res["status"] == "imported"
        assert res["imported_parties"] == 2



