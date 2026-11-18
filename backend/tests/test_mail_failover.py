import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ledgerpilot.config import Settings
from ledgerpilot.identity.mail import deliver
from ledgerpilot.shared.errors import DomainError


def dummy_settings(**kwargs) -> Settings:
    defaults = {
        "auth_delivery_enabled": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_from": "noreply@balanc.local",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_resend_fails_falls_through_to_brevo(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "resend-fake-key")
    monkeypatch.setenv("BREVO_API_KEY", "brevo-fake-key")

    calls = []

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        calls.append(url)
        if "resend.com" in url:
            fp = MagicMock()
            fp.read.return_value = (
                b'{"message": "You can only send testing emails to your own email address '
                b'(owner@example.com)"}'
            )
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs={}, fp=fp)
        if "brevo.com" in url:
            resp = MagicMock()
            resp.status = 201
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp
        raise RuntimeError(f"Unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        deliver(dummy_settings(), "newuser@example.com", "verify", "123456")

    assert any("resend.com" in c for c in calls)
    assert any("brevo.com" in c for c in calls)


def test_resend_and_brevo_fail_falls_through_to_sendgrid(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "resend-fake-key")
    monkeypatch.setenv("BREVO_API_KEY", "brevo-fake-key")
    monkeypatch.setenv("SENDGRID_API_KEY", "sendgrid-fake-key")

    calls = []

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        calls.append(url)
        if "resend.com" in url:
            fp = MagicMock()
            fp.read.return_value = b'{"message": "Sandbox blocked"}'
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs={}, fp=fp)
        if "brevo.com" in url:
            fp = MagicMock()
            fp.read.return_value = b'{"message": "Invalid key"}'
            raise urllib.error.HTTPError(url, 401, "Unauthorized", hdrs={}, fp=fp)
        if "sendgrid.com" in url:
            resp = MagicMock()
            resp.status = 202
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp
        raise RuntimeError(f"Unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        deliver(dummy_settings(), "newuser@example.com", "verify", "123456")

    assert len(calls) == 3


def test_all_fail_aggregates_errors_and_hints(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "resend-fake-key")
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)

    def fake_urlopen(req, timeout=15):
        fp = MagicMock()
        fp.read.return_value = (
            b'{"message": "You can only send testing emails to your own email address '
            b'(owner@example.com)"}'
        )
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs={}, fp=fp)

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch("smtplib.SMTP", side_effect=OSError("Network is unreachable")),
        pytest.raises(DomainError) as exc_info,
    ):
        deliver(dummy_settings(), "user@example.com", "verify", "123456")

    assert exc_info.value.code == "ERR_MAIL_UNAVAILABLE"
    assert "Resend (403)" in str(exc_info.value.message)
    assert "SMTP (smtp.gmail.com:587): Network is unreachable" in str(exc_info.value.message)
    assert "HINT: Resend test sandbox restriction active" in str(exc_info.value.message)
