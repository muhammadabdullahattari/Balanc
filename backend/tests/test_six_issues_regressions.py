"""Regression tests for the 6 production UX/media/email/timezone/voice issues."""

from __future__ import annotations

import base64
import os
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)

from ledgerpilot.ai.adapter import GroqModelAdapter
from ledgerpilot.config import Settings, get_settings
from ledgerpilot.identity.mail import deliver_workspace_email, format_mail_error_for_user
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.shared.security_headers import security_headers
from ledgerpilot.tenancy.schemas import BusinessCreate
from ledgerpilot.workspace.p3_automation import ReminderPreferenceInput

# ruff: noqa: F811 -- isolated real-service fixtures
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1",
    reason="Requires local PostgreSQL/SMTP",
)


def test_permissions_policy_allows_same_origin_microphone() -> None:
    headers = security_headers(Settings(), "https", "/api/v1/health")
    assert "microphone=(self)" in headers["Permissions-Policy"]
    assert "camera=()" in headers["Permissions-Policy"]


def test_iana_timezone_validation_on_business_and_reminder_preferences() -> None:
    biz = BusinessCreate(
        name="Karachi Ledger",
        currency="PKR",
        timezone="  Asia/Karachi  ",
        basis="accrual",
        fiscal_month=7,
        jurisdiction="PK",
    )
    assert biz.timezone == "Asia/Karachi"

    utc_biz = BusinessCreate(
        name="UTC Ledger",
        currency="USD",
        timezone="UTC",
        basis="accrual",
        fiscal_month=1,
        jurisdiction="US",
    )
    assert utc_biz.timezone == "UTC"

    with pytest.raises(ValidationError):
        BusinessCreate(
            name="Invalid TZ",
            currency="USD",
            timezone="Karachi Standard Time +5",
            basis="accrual",
            fiscal_month=1,
            jurisdiction="US",
        )

    pref = ReminderPreferenceInput(
        recipient_key="vendor-1",
        recipient_name="Vendor Billing",
        timezone="Asia/Karachi",
    )
    assert pref.timezone == "Asia/Karachi"

    with pytest.raises(ValidationError):
        ReminderPreferenceInput(
            recipient_key="vendor-1",
            recipient_name="Vendor Billing",
            timezone="Not/A_Real_Zone",
        )


def test_groq_whisper_normalizes_codec_mime_and_sets_zero_temperature() -> None:
    captured: dict[str, bytes] = {}

    def fake_sender(req: object, _timeout: float) -> tuple[int, dict[str, str], bytes]:
        data = getattr(req, "data", b"")
        captured["body"] = data if isinstance(data, bytes) else b""
        return 200, {}, b'{"text": "Record electricity bill from K-Electric for 850 USD"}'

    settings = Settings(
        app_env="local",
        groq_api_key=SecretStr("gsk_test_key"),
    )
    adapter = GroqModelAdapter(settings, http_sender=fake_sender)
    raw_b64 = base64.b64encode(b"WEBM_OPUS_TEST_AUDIO_BYTES_1234567890").decode("ascii")
    res = adapter.transcribe_audio(
        audio_base64=raw_b64,
        audio_format="audio/webm;codecs=opus",
        language_locale="en_US",
        fallback_transcript="fallback",
    )
    assert res.fallback_used is False
    assert "K-Electric" in res.transcript
    assert b"Content-Type: audio/webm\r\n" in captured["body"]
    assert b'name="temperature"\r\n\r\n0\r\n' in captured["body"]


def test_workspace_email_never_reports_success_when_unconfigured_or_rejected() -> None:
    unconfigured = Settings(
        app_env="local",
        auth_delivery_enabled=False,
        smtp_host="127.0.0.1",
        smtp_username="",
    )
    with patch.dict(
        "os.environ",
        {"RESEND_API_KEY": "", "BREVO_API_KEY": "", "SENDGRID_API_KEY": ""},
        clear=False,
    ):
        ok, mode, errs = deliver_workspace_email(
            unconfigured,
            "client@example.com",
            "Missing invoice",
            "Please share the invoice PDF.",
            tag="test",
        )
    assert ok is False
    assert mode == "unconfigured"
    assert errs

    bad_addr_ok, bad_mode, _ = deliver_workspace_email(
        unconfigured,
        "not-an-email",
        "Subject",
        "Body",
        tag="test",
    )
    assert bad_addr_ok is False
    assert bad_mode == "invalid_recipient"

    friendly = format_mail_error_for_user(
        ["SMTP (smtp.gmail.com:587): [Errno 101] Network is unreachable"]
    )
    assert "SMTP" not in friendly
    assert "mail server" in friendly.lower()


@REAL
async def test_clarification_email_and_voice_session_stability_end_to_end(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base = f"/api/v1/businesses/{bid}/ai"

        # 1. Voice session returns cached reply for same recording_id until regenerate=True
        v1 = await api.post(
            f"{base}/voice-session",
            json={
                "language_locale": "en_US",
                "transcript_input": "Record utility bill from K-Electric for 850.00 USD",
                "recording_id": "rec_fixed_test_clip_01",
                "regenerate": False,
            },
        )
        assert v1.status_code == 201, v1.text
        v1_body = v1.json()
        assert v1_body["cached"] is False
        assert v1_body["recording_id"] == "rec_fixed_test_clip_01"

        v2 = await api.post(
            f"{base}/voice-session",
            json={
                "language_locale": "en_US",
                "transcript_input": "Different accidental text on retry",
                "recording_id": "rec_fixed_test_clip_01",
                "regenerate": False,
            },
        )
        assert v2.status_code == 201, v2.text
        v2_body = v2.json()
        assert v2_body["cached"] is True
        assert v2_body["id"] == v1_body["id"]
        assert v2_body["tts_readback_text"] == v1_body["tts_readback_text"]

        v3 = await api.post(
            f"{base}/voice-session",
            json={
                "language_locale": "en_US",
                "transcript_input": "Record utility bill from K-Electric for 920.00 USD",
                "recording_id": "rec_fixed_test_clip_01",
                "regenerate": True,
            },
        )
        assert v3.status_code == 201, v3.text
        assert v3.json()["cached"] is False
        assert v3.json()["id"] == v1_body["id"]
        assert v3.json()["extracted_entities"]["amount"] == "920.00"

        # 2. Email clarification fails honestly (502) on error and marks 'sent' when confirmed
        live_mail_settings = database_settings.model_copy(update={"auth_delivery_enabled": True})
        from ledgerpilot.main import create_app

        live_app = create_app(live_mail_settings)
        async with (
            live_app.router.lifespan_context(live_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=live_app),
                base_url="https://api.ledgerpilot.test",
                cookies=api.cookies,
                headers=dict(api.headers),
            ) as live_client,
        ):
            with patch(
                "ledgerpilot.ai.router.deliver_workspace_email",
                return_value=(False, "failed", ["Connection refused by mail server"]),
            ):
                failed_clar = await live_client.post(
                    f"{base}/clarifications",
                    json={
                        "exception_kind": "missing_invoice",
                        "exception_ref": "BILL-FAIL-901",
                        "recipient_key": "client@example.com",
                        "channel": "email",
                        "context_summary": "Need November receipt",
                    },
                )
            assert failed_clar.status_code == 502
            assert failed_clar.json()["error"]["code"] == "ERR_EMAIL_DELIVERY_FAILED"

            with patch(
                "ledgerpilot.ai.router.deliver_workspace_email",
                return_value=(True, "brevo", []),
            ):
                ok_clar = await live_client.post(
                    f"{base}/clarifications",
                    json={
                        "exception_kind": "missing_invoice",
                        "exception_ref": "BILL-OK-902",
                        "recipient_key": "client@example.com",
                        "channel": "email",
                        "context_summary": "Need November receipt",
                    },
                )
            assert ok_clar.status_code == 201, ok_clar.text
            assert ok_clar.json()["status"] == "sent"
