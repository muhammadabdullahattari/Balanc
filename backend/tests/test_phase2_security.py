"""Service-independent security and exact-money boundaries."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import jwt
import pyotp
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ledgerpilot.accounting.money import allocate
from ledgerpilot.accounting.reconciliation import reconcile_control
from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.accounting.service import round_money
from ledgerpilot.identity import security
from ledgerpilot.identity.security import (
    canonical_email,
    decode_access,
    encode_access,
    normalized_password,
)
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.middleware import excessive_depth


def configured() -> FileSettings:
    return FileSettings.model_validate(
        {
            "app_env": "test",
            "database_url": "postgresql+psycopg://ledgerpilot_app:testing-password@127.0.0.1/test",
            "session_signing_key": "test-signing-key-longer-than-thirty-two-bytes",
        }
    )


def test_email_canonicalization_preserves_local_case_and_aliases() -> None:
    assert canonical_email("  Alice+books@EXAMPLE.COM ") == "Alice+books@example.com"
    assert canonical_email("a@bücher.de") == "a@xn--bcher-kva.de"
    with pytest.raises(DomainError):
        canonical_email("no-at-sign")


def test_password_policy_preserves_spaces_and_rejects_common_credentials() -> None:
    assert normalized_password("   strong private phrase   ") == "   strong private phrase   "
    with pytest.raises(DomainError):
        normalized_password("passwordpassword")
    with pytest.raises(DomainError):
        normalized_password("x" * 129)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "1e2", "-1", "0.0000001", 1.25])
def test_rejects_noncanonical_money(amount: object) -> None:
    with pytest.raises(ValidationError):
        LineCreate.model_validate({"account_id": str(uuid4()), "debit": amount})


def test_money_uses_explicit_half_even_rounding() -> None:
    assert round_money(Decimal("1.005"), 2) == Decimal("1.00")
    assert round_money(Decimal("1.015"), 2) == Decimal("1.02")


def test_remainder_allocation_is_exact_and_stable() -> None:
    result = allocate(Decimal("100"), [Decimal(1)] * 3, 2)
    assert result == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(result) == Decimal("100")


def test_control_reconciliation_requires_complete_sources_and_flags_difference() -> None:
    with pytest.raises(DomainError):
        reconcile_control(Decimal(100), [], source_complete=False)
    assert reconcile_control(
        Decimal(60), [Decimal(100), Decimal(-40)], source_complete=True
    ).balanced
    assert reconcile_control(
        Decimal(-80), [Decimal(-100), Decimal(20)], source_complete=True
    ).balanced
    assert reconcile_control(
        Decimal(61), [Decimal(60)], source_complete=True
    ).difference == Decimal(1)


def test_jwt_requires_signed_claims_and_explicit_key_id() -> None:
    settings = configured()
    uid, sid = uuid4(), uuid4()
    token = encode_access(settings, uid, sid)
    assert decode_access(settings, token) == (uid, sid)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 600
    for field, value in [("iss", "other"), ("aud", "other"), ("exp", 1)]:
        changed = dict(claims)
        changed[field] = value
        invalid = jwt.encode(
            changed,
            settings.session_signing_key.get_secret_value(),
            algorithm="HS256",
            headers={"kid": settings.jwt_key_id},
        )
        with pytest.raises(DomainError):
            decode_access(settings, invalid)
    with pytest.raises(DomainError):
        decode_access(settings, token + "tampered")


@given(st.integers(0, 100000000), st.lists(st.integers(1, 1000), min_size=1, max_size=20))
def test_allocation_always_preserves_the_approved_total(cents: int, weights: list[int]) -> None:
    amount = Decimal(cents) / 100
    result = allocate(amount, [Decimal(w) for w in weights], 2)
    assert sum(result) == amount
    assert all(value >= 0 and value == value.quantize(Decimal(".01")) for value in result)


def test_totp_counter_is_consumed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    instant = datetime(2026, 10, 29, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(security, "now", lambda: instant)
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).at(instant)
    counter = security.totp_counter(secret, code, -1)
    with pytest.raises(DomainError):
        security.totp_counter(secret, code, counter)


def test_json_depth_guard_ignores_brackets_inside_strings() -> None:
    assert not excessive_depth(b'{"memo":"[[[[[[[[[[[[[[[[[[[[[[[[["}')
    assert excessive_depth(b"[" * 11 + b"0" + b"]" * 11)


def test_password_hash_bulkhead_rejects_overload_without_waiting() -> None:
    security.PASSWORD_SLOTS.acquire()
    security.PASSWORD_SLOTS.acquire()
    try:
        with pytest.raises(DomainError) as busy:
            security.hash_password("A private valid password phrase")
        assert busy.value.status == 503
    finally:
        security.PASSWORD_SLOTS.release()
        security.PASSWORD_SLOTS.release()
