"""End-to-end tests for P3 Features C, D, and E."""

# ruff: noqa: E501

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401

from ledgerpilot.config import Settings
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.p3_automation import (
    _build_remittance_advice,
    _is_in_quiet_hours,
    _send_slack_reminder_message,
    _send_whatsapp_cloud_message,
    compute_totp_code,
    decrypt_secret,
    encrypt_secret,
    generate_base32_totp_seed,
    normalize_base32_seed,
)


def test_rfc6238_32_char_totp_seed_generation_and_6_digit_calculation() -> None:
    seed = generate_base32_totp_seed(32)
    assert len(seed) == 32
    assert normalize_base32_seed(seed) == seed

    fixed_seed = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    code1, rem1 = compute_totp_code(fixed_seed, timestamp=1700000000)
    code2, rem2 = compute_totp_code(fixed_seed, timestamp=1700000000)
    assert len(code1) == 6 and code1.isdigit()
    assert code1 == code2
    assert 1 <= rem1 <= 30

    with pytest.raises(DomainError) as exc:
        normalize_base32_seed("TOO-SHORT")
    assert exc.value.status == 422


def test_fernet_encryption_roundtrip_for_portal_credentials() -> None:
    settings = Settings()
    raw_seed = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    encrypted = encrypt_secret(settings, raw_seed)
    assert encrypted != raw_seed
    assert raw_seed not in encrypted
    assert decrypt_secret(settings, encrypted) == raw_seed


def test_quiet_hours_wraparound_and_linear_windows() -> None:
    # Overnight window 22:00 -> 07:00
    assert _is_in_quiet_hours(23, 22, 7) is True
    assert _is_in_quiet_hours(2, 22, 7) is True
    assert _is_in_quiet_hours(14, 22, 7) is False
    # Daytime window 9:00 -> 17:00
    assert _is_in_quiet_hours(12, 9, 17) is True
    assert _is_in_quiet_hours(20, 9, 17) is False


def test_whatsapp_and_slack_sandbox_dispatch_in_test_env() -> None:
    settings = Settings(app_env="test")
    wa_id, wa_meta = _send_whatsapp_cloud_message(
        settings, "+923412685261", "Overdue bill reminder", "hello_world"
    )
    assert wa_id.startswith("wamid.sandbox.")
    assert wa_meta["recipient"] == "923412685261"

    slack_id, slack_meta = _send_slack_reminder_message(
        settings, "#all-balanc", "Overdue bill reminder"
    )
    assert slack_id.startswith("slack.sandbox.")
    assert slack_meta["channel"] == "#all-balanc"


def test_remittance_advice_formats_for_stripe_ach_and_swift() -> None:
    stripe_remit = _build_remittance_advice(
        rail="stripe_live",
        provider_ref="pi_3Test12345",
        supplier_name="Amazon Web Services",
        bill_reference="BILL-AWS-101",
        amount=Decimal("425.00"),
        currency="USD",
        bank_name="Stripe Treasury",
        routing_or_swift="021000021",
        account_masked="••••4321",
        approved_by="owner@balanc.local",
    )
    assert "STRIPE TREASURY & PAYOUT REMITTANCE ADVICE" in stripe_remit
    assert "pi_3Test12345" in stripe_remit

    ach_remit = _build_remittance_advice(
        rail="ach_nacha",
        provider_ref="ACH-NACHA-20261124-ABCD1234",
        supplier_name="K-Electric Utility",
        bill_reference="BILL-KE-202",
        amount=Decimal("800.00"),
        currency="USD",
        bank_name="JPMorgan Chase",
        routing_or_swift="021000021",
        account_masked="••••4321",
        approved_by="owner@balanc.local",
    )
    assert "NACHA BATCH PAYLOAD" in ach_remit

    swift_remit = _build_remittance_advice(
        rail="swift_wire",
        provider_ref="SWIFT-MT103-20261124-EFGH5678",
        supplier_name="Global Telecom Ltd",
        bill_reference="BILL-TEL-303",
        amount=Decimal("1250.00"),
        currency="USD",
        bank_name="Citibank",
        routing_or_swift="CITIUS33XXX",
        account_masked="••••9876",
        approved_by="owner@balanc.local",
    )
    assert "SWIFT MT103 INTERNATIONAL WIRE REMITTANCE" in swift_remit
    assert ":23B:CRED" in swift_remit


def test_p3_cde_live_stripe_slack_and_whatsapp_integration_with_totp() -> None:
    """Verify the live provider helpers and 32-char TOTP portal authenticator."""
    from ledgerpilot.workspace.p3_automation import _execute_live_stripe_supplier_settlement

    # 1. 32-character TOTP seed calculation for Method B Supplier Portals
    portal_seed = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    totp_code, rem = compute_totp_code(portal_seed)
    assert len(totp_code) == 6 and totp_code.isdigit()
    assert 1 <= rem <= 30

    # 2. Test-mode deterministic Stripe settlement rail
    test_settings = Settings(app_env="test")
    pi_id, payload = _execute_live_stripe_supplier_settlement(
        settings=test_settings,
        amount=Decimal("425.00"),
        currency="USD",
        supplier_name="Amazon Web Services Inc.",
        bill_reference="BILL-AWS-2026-114",
        beneficiary_account_masked="••••4321",
        idempotency_key="test-idem-key-114",
    )
    assert pi_id.startswith("pi_")
    assert payload["status"] == "succeeded"
    assert payload["amount_cents"] == 42500


@pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1",
    reason="Requires live PostgreSQL RLS database",
)
async def test_p3_cde_end_to_end_under_real_postgres_rls(
    client: httpx.AsyncClient,  # noqa: F811
    database_settings: FileSettings,  # noqa: F811
) -> None:
    async with accountant_context(client, database_settings) as (bid, accountant):
        headers = {"X-CSRF-Token": accountant.headers.get("X-CSRF-Token", "")}

        # =====================================================================
        # 1. Feature C (P3-B): Multichannel WhatsApp & Slack Reminders
        # =====================================================================
        chan_resp = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/channels",
            headers=headers,
            json={
                "whatsapp_phone_number_id": "1391722684032246",
                "whatsapp_business_account_id": "1877048859962524",
                "whatsapp_access_token": "EAAaCustomWorkspaceWhatsAppToken1234567890",
                "default_whatsapp_recipient": "+923412685261",
                "slack_workspace_name": "Acme Finance Slack",
                "slack_webhook_url": "https://hooks.slack.com/services/T000/B000/CustomSecret",
                "default_slack_channel": "#all-balanc",
            },
        )
        assert chan_resp.status_code == 200, chan_resp.text
        chan_data = chan_resp.json()
        assert chan_data["whatsapp_mode"] == "workspace_custom"
        assert chan_data["slack_mode"] == "workspace_custom"
        assert "••••••••" in chan_data["whatsapp_token_masked"]
        assert "••••••••" in chan_data["slack_credentials_masked"]

        pref_resp = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/preferences",
            headers=headers,
            json={
                "recipient_key": "vendor-ap-contact",
                "recipient_name": "Finance Approver",
                "whatsapp_phone": "+923412685261",
                "slack_channel": "#all-balanc",
                "email_address": "finance@balanc.local",
                "whatsapp_opt_in": True,
                "slack_opt_in": True,
                "email_fallback": True,
                "channel_order": ["whatsapp", "slack", "email"],
                "quiet_start_hour": 22,
                "quiet_end_hour": 7,
                "timezone": "Asia/Karachi",
                "locale": "en_US",
                "max_attempts": 2,
                "opted_out": False,
            },
        )
        assert pref_resp.status_code == 200, pref_resp.text

        list_rem = await accountant.get(f"/api/v1/businesses/{bid}/p3/reminders")
        assert list_rem.status_code == 200, list_rem.text
        assert all(
            p["recipient_key"] != "__workspace_channel_connector__"
            for p in list_rem.json()["preferences"]
        )

        target_bill_id = str(uuid4())
        # Attempt #1 via WhatsApp (inside active daytime hour 14)
        rem1 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "bill",
                "target_id": target_bill_id,
                "target_label": "AWS Cloud Bill #INV-101 ($425.00)",
                "recipient_key": "vendor-ap-contact",
                "channel": "whatsapp",
                "idempotency_key": "rem-test-key-1",
                "current_hour_override": 14,
            },
        )
        assert rem1.status_code == 200, rem1.text
        rem1_data = rem1.json()
        assert rem1_data["status"] == "sent"
        assert rem1_data["channel"] == "whatsapp"
        assert rem1_data["attempt_number"] == 1
        assert rem1_data["provider_message_id"].startswith("wamid.")

        # Idempotent replay with same idempotency_key
        rem1_replay = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "bill",
                "target_id": target_bill_id,
                "target_label": "AWS Cloud Bill #INV-101 ($425.00)",
                "recipient_key": "vendor-ap-contact",
                "channel": "whatsapp",
                "idempotency_key": "rem-test-key-1",
            },
        )
        assert rem1_replay.status_code == 200
        assert rem1_replay.json()["idempotent_replay"] is True

        # Quiet hours check (hour 23 is inside 22..7)
        rem_quiet = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "bill",
                "target_id": target_bill_id,
                "target_label": "AWS Cloud Bill #INV-101 ($425.00)",
                "recipient_key": "vendor-ap-contact",
                "channel": "slack",
                "current_hour_override": 23,
            },
        )
        assert rem_quiet.status_code == 200
        assert rem_quiet.json()["status"] == "skipped_quiet_hours"

        # Resolution recheck (no reminder sent if already paid/resolved)
        rem_resolved = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "bill",
                "target_id": target_bill_id,
                "target_label": "AWS Cloud Bill #INV-101 ($425.00)",
                "recipient_key": "vendor-ap-contact",
                "channel": "slack",
                "is_resolved_override": True,
            },
        )
        assert rem_resolved.status_code == 200
        assert rem_resolved.json()["status"] == "skipped_resolved"

        # Attempt #2 via Slack (reaches max_attempts = 2)
        rem2 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "bill",
                "target_id": target_bill_id,
                "target_label": "AWS Cloud Bill #INV-101 ($425.00)",
                "recipient_key": "vendor-ap-contact",
                "channel": "slack",
                "current_hour_override": 15,
            },
        )
        assert rem2.status_code == 200
        assert rem2.json()["status"] == "sent"
        assert rem2.json()["attempt_number"] == 2

        # Attempt #3 hits max_attempts cap (2) -> skipped_cap_reached
        rem3 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "bill",
                "target_id": target_bill_id,
                "target_label": "AWS Cloud Bill #INV-101 ($425.00)",
                "recipient_key": "vendor-ap-contact",
                "channel": "whatsapp",
                "current_hour_override": 15,
            },
        )
        assert rem3.status_code == 200
        assert rem3.json()["status"] == "skipped_cap_reached"

        # Opt-out blocks future reminders
        opt_out = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/opt-out",
            headers=headers,
            json={"recipient_key": "vendor-ap-contact"},
        )
        assert opt_out.status_code == 200
        rem_opted_out = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/reminders/dispatch",
            headers=headers,
            json={
                "target_kind": "invoice",
                "target_label": "New Invoice #202",
                "recipient_key": "vendor-ap-contact",
                "channel": "whatsapp",
            },
        )
        assert rem_opted_out.status_code == 200
        assert rem_opted_out.json()["status"] == "skipped_opted_out"

        # =====================================================================
        # 2. Feature D (P3-A): Supplier Portal Connectors (32-Char TOTP + OCR)
        # =====================================================================
        seed_resp = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-portals/generate-totp-seed",
            headers=headers,
        )
        assert seed_resp.status_code == 200
        seed_data = seed_resp.json()
        assert seed_data["seed_length"] == 32
        assert len(seed_data["current_6_digit_code"]) == 6

        portal_resp = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-portals",
            headers=headers,
            json={
                "vendor_name": "K-Electric Utility Portal",
                "portal_kind": "utility_portal",
                "portal_url": "https://billing.ke.com.pk/viewer/invoices",
                "login_username": "viewer@balanc.local",
                "password": "SuperSecretViewerPassword!",
                "totp_enabled": True,
                "totp_seed": seed_data["totp_seed"],
                "schedule_frequency": "monthly",
            },
        )
        assert portal_resp.status_code == 200, portal_resp.text
        conn_id = portal_resp.json()["id"]

        # Sync invoice #1 -> calculates live 6-digit TOTP code and creates Purchase Bill Draft
        sync1 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-portals/{conn_id}/sync",
            headers=headers,
            json={
                "external_invoice_id": "KE-2026-11-001",
                "invoice_number": "INV-KE-9901",
                "total_amount": "640.00",
                "tax_amount": "40.00",
                "currency": "USD",
            },
        )
        assert sync1.status_code == 200, sync1.text
        sync1_data = sync1.json()
        assert sync1_data["status"] == "ingested_to_draft"
        assert sync1_data["duplicate_skipped"] is False
        assert len(sync1_data["totp_6_digit_code_used"]) == 6
        assert sync1_data["provenance"]["ledger_posting"] == "blocked_until_human_review"

        # Duplicate replay of the same invoice is safely skipped
        sync_dup = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-portals/{conn_id}/sync",
            headers=headers,
            json={
                "external_invoice_id": "KE-2026-11-001",
                "invoice_number": "INV-KE-9901",
                "total_amount": "640.00",
                "tax_amount": "40.00",
                "currency": "USD",
            },
        )
        assert sync_dup.status_code == 200
        assert sync_dup.json()["duplicate_skipped"] is True
        assert sync_dup.json()["status"] == "duplicate_skipped"

        # Revoking portal wipes encrypted credentials and blocks future syncs
        rev_resp = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-portals/{conn_id}/revoke",
            headers=headers,
        )
        assert rev_resp.status_code == 200
        sync_after_revoke = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-portals/{conn_id}/sync",
            headers=headers,
            json={"external_invoice_id": "KE-2026-11-002"},
        )
        assert sync_after_revoke.status_code == 409

        # =====================================================================
        # 3. Feature E (P3-D): Human-Approved Supplier Payments
        # =====================================================================
        ben_resp = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/beneficiaries",
            headers=headers,
            json={
                "supplier_name": "Amazon Web Services Inc.",
                "account_holder_name": "AWS Treasury Operations",
                "bank_name": "JPMorgan Chase / Stripe Treasury",
                "routing_or_swift": "021000021",
                "account_number": "882910444321",
                "currency": "USD",
                "rail": "stripe_live",
                "out_of_band_verified": True,
                "verification_reference": "Verified via vendor callback",
            },
        )
        assert ben_resp.status_code == 200, ben_resp.text
        ben_id = ben_resp.json()["id"]

        # Prepare a payment draft #1 ($425.00) and then test Account-Takeover Beneficiary-Change Invalidation
        prep1 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/prepare",
            headers=headers,
            json={
                "bill_reference": "BILL-AWS-TAKEOVER-TEST",
                "supplier_name": "Amazon Web Services Inc.",
                "beneficiary_id": ben_id,
                "amount": "425.00",
                "currency": "USD",
                "rail": "stripe_live",
            },
        )
        assert prep1.status_code == 200
        pay1_id = prep1.json()["id"]
        assert prep1.json()["state"] == "prepared"

        # Modify beneficiary bank account -> must invalidate pending pay1_id!
        upd_bank = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/beneficiaries/{ben_id}/update-bank",
            headers=headers,
            json={
                "routing_or_swift": "021000021",
                "account_number": "999900007777",
                "out_of_band_verified": True,
            },
        )
        assert upd_bank.status_code == 200
        assert upd_bank.json()["invalidated_pending_payments"] == 1

        # Trying to approve invalidated payment fails with 409 ERR_CONFLICT
        exec_invalid = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{pay1_id}/approve-and-execute",
            headers=headers,
            json={"step_up_code": "123456"},
        )
        assert exec_invalid.status_code == 409

        # Prepare a fresh payment draft #2 ($425.00) against the updated v2 beneficiary
        prep2 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/prepare",
            headers=headers,
            json={
                "bill_reference": "BILL-AWS-SETTLE-101",
                "supplier_name": "Amazon Web Services Inc.",
                "beneficiary_id": ben_id,
                "amount": "425.00",
                "currency": "USD",
                "rail": "stripe_live",
            },
        )
        assert prep2.status_code == 200
        pay2_id = prep2.json()["id"]

        # Missing/bad step-up code is rejected with 403
        bad_stepup = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{pay2_id}/approve-and-execute",
            headers=headers,
            json={"step_up_code": "bad!"},
        )
        assert bad_stepup.status_code == 403

        # Emergency Kill Switch blocks execution when active
        await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/controls",
            headers=headers,
            json={
                "kill_switch_active": True,
                "kill_switch_reason": "Security drill pause",
                "single_payment_limit": "50000.00",
                "dual_approval_threshold": "5000.00",
                "default_rail": "stripe_live",
            },
        )
        blocked_by_ks = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{pay2_id}/approve-and-execute",
            headers=headers,
            json={"step_up_code": "123456"},
        )
        assert blocked_by_ks.status_code == 409

        # Deactivate Kill Switch and execute payment #2 with valid 6-digit Step-Up TOTP code!
        await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/controls",
            headers=headers,
            json={
                "kill_switch_active": False,
                "kill_switch_reason": "",
                "single_payment_limit": "50000.00",
                "dual_approval_threshold": "5000.00",
                "default_rail": "stripe_live",
            },
        )
        exec_ok = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{pay2_id}/approve-and-execute",
            headers=headers,
            json={"step_up_code": "123456"},
        )
        assert exec_ok.status_code == 200, exec_ok.text
        exec_data = exec_ok.json()
        assert exec_data["state"] == "settled_posted"
        assert exec_data["provider_reference"].startswith("pi_")
        assert "STRIPE TREASURY & PAYOUT REMITTANCE ADVICE" in exec_data["remittance_advice"]

        # Dual approval test for payment >= $5,000.00 ($6,200.00 via NACHA ACH)
        prep_dual = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/prepare",
            headers=headers,
            json={
                "bill_reference": "BILL-AWS-HIGHVAL-202",
                "supplier_name": "Amazon Web Services Inc.",
                "beneficiary_id": ben_id,
                "amount": "6200.00",
                "currency": "USD",
                "rail": "ach_nacha",
            },
        )
        assert prep_dual.status_code == 200
        assert prep_dual.json()["requires_dual_approval"] is True
        dual_id = prep_dual.json()["id"]

        # First human sign-off transitions to first_approved
        sign1 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{dual_id}/approve-and-execute",
            headers=headers,
            json={"step_up_code": "123456"},
        )
        assert sign1.status_code == 200
        assert sign1.json()["state"] == "first_approved"

        # Same human attempting second sign-off in a multi-approver workspace is blocked (403)
        same_user_sign2 = await accountant.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{dual_id}/approve-and-execute",
            headers=headers,
            json={"step_up_code": "654321"},
        )
        assert same_user_sign2.status_code == 403

        # Distinct second human (workspace owner `client`) completes final sign-off
        owner_headers = {"X-CSRF-Token": client.headers.get("X-CSRF-Token", "")}
        sign2 = await client.post(
            f"/api/v1/businesses/{bid}/p3/supplier-payments/{dual_id}/approve-and-execute",
            headers=owner_headers,
            json={"step_up_code": "654321"},
        )
        assert sign2.status_code == 200, sign2.text
        assert sign2.json()["state"] == "settled_posted"
        assert sign2.json()["provider_reference"].startswith("ACH-NACHA-")
        assert "NACHA BATCH PAYLOAD" in sign2.json()["remittance_advice"]
