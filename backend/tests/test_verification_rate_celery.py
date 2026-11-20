from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from ledgerpilot.config import Settings
from ledgerpilot.identity.models import User
from ledgerpilot.identity.service import (
    dispatch_identity_email,
)
from ledgerpilot.identity.verification_limiter import evaluate_verification_cooldown
from ledgerpilot.shared.errors import DomainError


def dummy_settings(**kwargs) -> Settings:
    defaults = {
        "auth_delivery_enabled": True,
        "verification_gap_1_seconds": 900,  # 15 mins
        "verification_gap_2_seconds": 1800,  # 30 mins
        "verification_gap_3_seconds": 3600,  # 1 hour
        "verification_daily_limit": 3,
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "smtp_from": "noreply@balanc.local",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_gap_schedule_15m_30m_1h_and_daily_cap():
    settings = dummy_settings()
    user = User(
        id=uuid4(),
        email="testgap@example.com",
        password_hash="fake",
        verification_attempts=0,
        verification_started_at=None,
        verification_blocked_until=None,
    )
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

    # 1st email: Allowed immediately
    evaluate_verification_cooldown(settings, user, user.email, t0)
    assert user.verification_attempts == 1
    assert user.verification_started_at == t0
    assert user.verification_blocked_until == t0 + timedelta(minutes=15)

    # 2nd email attempted at 5 minutes: Blocked (cooldown 15m)
    t_5m = t0 + timedelta(minutes=5)
    with pytest.raises(DomainError) as exc_info:
        evaluate_verification_cooldown(settings, user, user.email, t_5m)
    assert exc_info.value.code == "ERR_VERIFICATION_COOLDOWN"
    assert exc_info.value.retry_after == 600  # 10 minutes left
    assert "Please wait 10 minutes" in exc_info.value.message

    # 2nd email attempted at 15 minutes: Allowed!
    t_15m = t0 + timedelta(minutes=15)
    evaluate_verification_cooldown(settings, user, user.email, t_15m)
    assert user.verification_attempts == 2
    assert user.verification_blocked_until == t_15m + timedelta(minutes=30)

    # 3rd email attempted at 30 minutes (only 15 mins after 2nd): Blocked (cooldown 30m)
    t_30m = t0 + timedelta(minutes=30)
    with pytest.raises(DomainError) as exc_info:
        evaluate_verification_cooldown(settings, user, user.email, t_30m)
    assert exc_info.value.code == "ERR_VERIFICATION_COOLDOWN"
    assert exc_info.value.retry_after == 900  # 15 minutes left
    assert "Please wait 15 minutes" in exc_info.value.message

    # 3rd email attempted at 45 minutes (30 mins after 2nd): Allowed!
    t_45m = t_15m + timedelta(minutes=30)
    evaluate_verification_cooldown(settings, user, user.email, t_45m)
    assert user.verification_attempts == 3

    # 4th email attempted within 24h: Blocked by daily limit (Max 3 in 24h)
    t_2h = t0 + timedelta(hours=2)
    with pytest.raises(DomainError) as exc_info:
        evaluate_verification_cooldown(settings, user, user.email, t_2h)
    assert exc_info.value.code == "ERR_VERIFICATION_LIMIT"
    assert "Maximum 3 verification emails" in exc_info.value.message

    # Reset after 24 hours: Allowed!
    t_25h = t0 + timedelta(hours=25)
    evaluate_verification_cooldown(settings, user, user.email, t_25h)
    assert user.verification_attempts == 1
    assert user.verification_started_at == t_25h


def test_redis_caching_and_sync():
    settings = dummy_settings()
    user = User(
        id=uuid4(),
        email="redis_test@example.com",
        password_hash="fake",
        verification_attempts=0,
    )
    t0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)

    mock_redis = MagicMock()
    mock_redis.get.return_value = None

    with patch(
        "ledgerpilot.identity.verification_limiter.get_redis_client",
        return_value=mock_redis,
    ):
        evaluate_verification_cooldown(settings, user, user.email, t0)
        assert mock_redis.set.called
        key_called, payload_called = mock_redis.set.call_args[0][:2]
        assert "redis_test@example.com" in key_called
        assert '"attempts": 1' in payload_called


def test_celery_task_and_fallback():
    settings = dummy_settings()
    recipient = "celery_user@example.com"
    purpose = "verify"
    token = "654321"

    # 1. When Celery delay succeeds
    with (
        patch("ledgerpilot.identity.tasks.send_verification_email_task.delay") as mock_delay,
        patch("ledgerpilot.identity.mail.deliver") as mock_deliver,
    ):
        dispatch_identity_email(settings, recipient, purpose, token)
        assert mock_delay.called
        assert not mock_deliver.called

    # 2. When Celery delay raises exception (fallback to direct mail.deliver)
    with (
        patch(
            "ledgerpilot.identity.tasks.send_verification_email_task.delay",
            side_effect=Exception("Redis broker connection refused"),
        ),
        patch("ledgerpilot.identity.mail.deliver") as mock_deliver,
    ):
        dispatch_identity_email(settings, recipient, purpose, token)
        assert mock_deliver.called
        mock_deliver.assert_called_once_with(settings, recipient, purpose, token)
