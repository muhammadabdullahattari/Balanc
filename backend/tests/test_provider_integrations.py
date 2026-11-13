import hashlib
import hmac
import time

import pytest

from ledgerpilot.provider_integrations import signed_event
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
