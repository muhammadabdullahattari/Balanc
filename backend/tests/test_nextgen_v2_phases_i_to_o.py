"""Comprehensive unit & contract verification for Next-Gen v2.0 Phases I through O."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4
from zipfile import ZipFile

import pytest

from ledgerpilot.accounting.macrs import (
    compute_dual_depreciation_schedule,
    scan_capitalization_candidates,
)
from ledgerpilot.banking.merchants import (
    brandfetch_logo_url,
    build_double_entry_preview,
    clean_merchant_descriptor,
    normalize_merchant,
)
from ledgerpilot.banking.ofx_parser import ofx_to_rows, parse_ofx
from ledgerpilot.config import Settings
from ledgerpilot.connectors.payroll import parse_payroll_register_csv
from ledgerpilot.connectors.settlements import unbundle_settlement_csv, unbundle_settlement_rows
from ledgerpilot.documents.understanding import extract_service_period, extract_structured_document
from ledgerpilot.jobs.scheduler import compute_next_run
from ledgerpilot.reports.flux import (
    compute_flux_variances,
    generate_flux_report,
    verify_narrative_numbers,
)
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.trade.deductions import (
    classify_short_pay_remittance,
    evaluate_short_pay,
    parse_payment_terms,
)
from ledgerpilot.trade.fraud_sentinel import (
    build_vapid_alert_payload,
    evaluate_vendor_fraud_risk,
    generate_local_vapid_keypair,
    validate_tax_id,
)
from ledgerpilot.workspace.accruals import build_accrual_journal_lines, next_month_first
from ledgerpilot.workspace.amortization import compute_amortization_installments
from ledgerpilot.workspace.chasing import (
    create_signed_upload_token,
    match_chased_receipt,
    verify_signed_upload_token,
)
from ledgerpilot.workspace.intercompany import build_intercompany_mirror_entries
from ledgerpilot.workspace.landed_cost import prorate_landed_cost
from ledgerpilot.workspace.workpapers import WORKPAPER_SHEETS, build_workpapers_xlsx


def _test_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+psycopg://ledgerpilot_app:synthetic@localhost/ledgerpilot",
        session_signing_key="test-key-" + ("a" * 32),
        frontend_origins="http://localhost:5173",
    )


# --- Phase I: Scheduler ---


def test_i1_scheduler_computes_next_run_across_cron_presets() -> None:
    now = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
    assert compute_next_run("document_chase_24h", now) > now
    assert compute_next_run("amortization_monthly", now).day == 1
    assert compute_next_run("accrual_day1_reversal", now).day == 1
    assert compute_next_run("accrual_month_end_scan", now) > now
    assert compute_next_run("flux_day1_analysis", now).day == 1


# --- Phase J: OFX/QFX Parser & AI-First Merchant Normalization ---


def test_j0_ofx_1x_sgml_and_2x_xml_parsing_and_validation() -> None:
    sgml = (
        "OFXHEADER:100\nDATA:OFXSGML\n"
        "<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD\n<BANKTRANLIST>\n"
        "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261115120000[-5:EST]"
        "<TRNAMT>-84.50<FITID>FIT001<NAME>AMZN MKTP US*2K4"
        "<MEMO>Office supplies</STMTTRN>\n"
        "<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261116"
        "<TRNAMT>1250.00<FITID>FIT002<NAME>STRIPE PAYOUT</STMTTRN>\n"
        "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    )
    rows = ofx_to_rows(sgml)
    assert len(rows) == 2
    assert rows[0]["external_id"] == "FIT001"
    assert rows[0]["amount"] == "-84.50"
    assert rows[0]["posted_date"] == "2026-11-15"

    xml_ofx = """<?xml version="1.0" encoding="UTF-8"?>
    <OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>EUR</CURDEF>
    <BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261120</DTPOSTED>
    <TRNAMT>-42.10</TRNAMT><FITID>XML001</FITID><NAME>UBER *TRIP</NAME></STMTTRN>
    </BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
    stmt = parse_ofx(xml_ofx)
    assert stmt.currency == "EUR"
    assert stmt.transactions[0].amount == Decimal("-42.10")

    with pytest.raises(DomainError):
        parse_ofx("not an ofx file at all")


def test_j1_ai_first_merchant_normalization_and_double_entry_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = [
        ("AMZN MKTP US*2K4", "Amazon Marketplace", "Office Supplies"),
        ("SQ *BOB CLEANING 90212", "Bob Cleaning", "Repairs & Maintenance"),
        ("GOOGLE *CLOUD 84738", "Google Cloud", "Cloud & Hosting"),
        ("MSFT *E0300X9", "Microsoft", "Software & Subscriptions"),
        ("UBER *TRIP 4491 CA", "Uber", "Travel & Lodging"),
    ]
    for raw, expected_name, expected_cat in samples:
        name, cat, _domain, conf = clean_merchant_descriptor(raw)
        assert expected_name.lower() in name.lower(), (raw, name)
        assert cat == expected_cat, (raw, cat)
        assert conf >= Decimal("0.70")

    preview = build_double_entry_preview(
        merchant_name="Amazon Marketplace",
        category="Office Supplies",
        amount=Decimal("-84.50"),
    )
    assert preview["balanced"] is True
    assert preview["total_debit"] == "84.50"
    assert preview["total_credit"] == "84.50"

    monkeypatch.delenv("BRANDFETCH_CLIENT_ID", raising=False)
    assert brandfetch_logo_url("amazon.com") is None
    monkeypatch.setenv("BRANDFETCH_CLIENT_ID", "demo-client")
    assert "cdn.brandfetch.io/amazon.com" in (brandfetch_logo_url("amazon.com") or "")

    norm = normalize_merchant(
        None, uuid4(), "AMZN MKTP US*2K4", amount=Decimal("-50.00"), use_llm=False
    )
    assert norm["normalized_name"] == "Amazon Marketplace"
    assert norm["journal_preview"]["balanced"] is True


# --- Phase K: Accruals, Service-Period Amortization & Day-1 Flux ---


def test_k1_accrual_journal_lines_and_reversal_date() -> None:
    assert next_month_first(date(2026, 11, 30)) == date(2026, 12, 1)
    assert next_month_first(date(2026, 12, 31)) == date(2027, 1, 1)
    accrual = build_accrual_journal_lines(amount=Decimal("4500.00"))
    reversal = build_accrual_journal_lines(amount=Decimal("4500.00"), reverse=True)
    assert accrual[0]["debit"] == "4500.00" and accrual[1]["credit"] == "4500.00"
    assert reversal[0]["account"] == "Accrued Liabilities" and reversal[0]["debit"] == "4500.00"


def test_k2_service_period_extraction_and_amortization_penny_tie_out() -> None:
    sp1 = extract_service_period("Annual coverage: Nov 1, 2026 - Oct 31, 2027. Total $1,000.00")
    assert sp1 is not None and sp1["start_date"] == "2026-11-01" and sp1["end_date"] == "2027-10-31"
    assert sp1["months"] == 12

    sp2 = extract_service_period("12-month subscription starting 2026-01-01")
    assert sp2 is not None and sp2["start_date"] == "2026-01-01" and sp2["months"] == 12

    doc = extract_structured_document(
        "Vendor: Acme Insurance\nInvoice #INV-99\n"
        "Coverage: 2026-01-01 to 2026-12-31\nTotal: 1000.00"
    )
    assert doc["service_period"] is not None

    installments = compute_amortization_installments(
        Decimal("1000.00"), date(2026, 1, 1), date(2026, 12, 31)
    )
    assert len(installments) == 12
    assert sum(Decimal(i["amount"]) for i in installments) == Decimal("1000.00")


def test_k3_day1_flux_materiality_and_hallucination_guard() -> None:
    cur = [
        {
            "account_code": "6100",
            "account_name": "Cloud Hosting",
            "account_type": "expense",
            "balance": "14000.00",
        },
        {
            "account_code": "6200",
            "account_name": "Office Supplies",
            "account_type": "expense",
            "balance": "520.00",
        },
    ]
    pm = [
        {
            "account_code": "6100",
            "account_name": "Cloud Hosting",
            "account_type": "expense",
            "balance": "10000.00",
        },
        {
            "account_code": "6200",
            "account_name": "Office Supplies",
            "account_type": "expense",
            "balance": "500.00",
        },
    ]
    variances = compute_flux_variances(cur, pm)
    assert variances[0]["account_code"] == "6100" and variances[0]["material"] is True
    assert variances[1]["material"] is False

    assert (
        verify_narrative_numbers(
            "Cloud Hosting increased by 4000.00 (40.00%) to 14000.00.", variances
        )
        is True
    )
    assert (
        verify_narrative_numbers("Cloud Hosting jumped by 99999.00 due to mystery fees.", variances)
        is False
    )

    rep = generate_flux_report(
        business_id=uuid4(),
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 30),
        current_balances=cur,
        prior_month_balances=pm,
    )
    assert rep["material_count"] == 1
    assert "Cloud Hosting" in rep["narrative"]


# --- Phase L: Email-First Chasing & AR Short-Pay Deductions ---


def test_l1_signed_upload_token_and_receipt_auto_matching() -> None:
    settings = _test_settings()
    bid, cid = uuid4(), uuid4()
    token, _exp = create_signed_upload_token(
        settings, business_id=bid, chase_event_id=cid, ttl_days=7
    )
    verified_bid, verified_cid = verify_signed_upload_token(settings, token)
    assert verified_bid == bid and verified_cid == cid

    with pytest.raises(DomainError):
        verify_signed_upload_token(settings, token + "tampered")

    m_good = match_chased_receipt(
        None,
        bid,
        expected_amount=Decimal("125.00"),
        extracted_amount=Decimal("125.00"),
        expected_vendor="Amazon Marketplace",
        extracted_vendor="Amazon",
    )
    assert m_good["auto_linked"] is True


def test_l2_short_pay_terms_validation_and_dispute_drafting() -> None:
    assert parse_payment_terms("2/10 Net 30")["discount_pct"] == Decimal("2.00")
    assert (
        classify_short_pay_remittance("Deducted for 4 damaged goods units")["reason"]
        == "damaged_goods"
    )

    in_terms = evaluate_short_pay(
        invoice_number="INV-1001",
        customer_name="Beta Corp",
        invoice_amount=Decimal("1000.00"),
        paid_amount=Decimal("980.00"),
        invoice_date=date(2026, 11, 1),
        payment_date=date(2026, 11, 8),
        payment_terms="2/10 Net 30",
        remittance_note="2/10 early pay discount",
    )
    assert in_terms["in_terms_discount"] is True
    assert in_terms["recommended_action"] == "post_sales_discount"

    late_discount = evaluate_short_pay(
        invoice_number="INV-1002",
        customer_name="Beta Corp",
        invoice_amount=Decimal("1000.00"),
        paid_amount=Decimal("980.00"),
        invoice_date=date(2026, 11, 1),
        payment_date=date(2026, 11, 25),
        payment_terms="2/10 Net 30",
        remittance_note="2/10 early pay discount",
    )
    assert late_discount["in_terms_discount"] is False
    assert late_discount["recommended_action"] == "leave_ar_open_and_dispute"
    assert late_discount["dispute_email_draft"] is not None


# --- Phase M: Payroll, Settlements & Intercompany ---


def test_m1_payroll_register_csv_balances_and_matches_bank_to_cent() -> None:
    csv_data = (
        "Department,Gross Pay,Employer Tax,Employee Tax,"
        "401k Employee,401k Employer,Health Employee,Health Employer,Net Pay\n"
        "Engineering,10000.00,765.00,2200.00,500.00,400.00,200.00,600.00,7100.00\n"
        "Sales,6000.00,459.00,1200.00,300.00,240.00,150.00,450.00,4350.00\n"
    )
    res = parse_payroll_register_csv(
        csv_data, provider="gusto", bank_withdrawal_amount=Decimal("11450.00")
    )
    assert res["balanced"] is True
    assert res["totals"]["401k_employer_match"] == "640.00"
    assert res["totals"]["health_employer"] == "1050.00"
    assert res["bank_match"]["matched_to_cent"] is True


def test_m2_paypal_and_amazon_settlement_unbundling_with_reserves() -> None:
    csv_data = (
        "type,gross,fee,refund,reserve_hold,reserve_release\n"
        "sale,5000.00,145.00,0.00,500.00,0.00\n"
        "refund,200.00,0.00,200.00,0.00,0.00\n"
        "reserve_release,0.00,0.00,0.00,0.00,250.00\n"
    )
    res = unbundle_settlement_csv("amazon", csv_data)
    assert res["balanced"] is True
    assert res["reserve_holds"] == "500.00"
    assert res["reserve_releases"] == "250.00"
    assert res["net_payout"] == "4405.00"

    pp = unbundle_settlement_rows(
        provider="paypal",
        rows=[{"type": "sale", "gross": "1000.00", "fee": "30.00", "reserve_hold": "100.00"}],
    )
    assert pp["balanced"] is True and pp["net_payout"] == "870.00"


def test_m3_intercompany_mirror_and_elimination() -> None:
    res = build_intercompany_mirror_entries(
        entity_a_name="Parent LLC",
        entity_b_name="Sub UK Ltd",
        amount=Decimal("25000.00"),
        currency="USD",
    )
    assert res["elimination_entry"]["eliminates_to_zero"] is True
    assert res["entity_a_entry"]["lines"][0]["debit"] == "25000.00"
    assert res["entity_b_mirror_entry"]["lines"][1]["credit"] == "25000.00"


# --- Phase N: Fraud Sentinel + Local VAPID, MACRS, Landed Cost & Excel Workpapers ---


def test_n1_fraud_sentinel_tax_validation_and_local_vapid() -> None:
    assert validate_tax_id("12-3456789", country_code="US")["valid"] is True
    assert validate_tax_id("00-1234567", country_code="US")["valid"] is False
    assert (
        validate_tax_id("DE123456789", country_code="DE", check_eu_vies_online=False)["valid"]
        is True
    )

    risk = evaluate_vendor_fraud_risk(
        vendor_name="Acme Supply",
        invoice_text="Urgent wire to new bank details! Routing: 021000021 Account: 9988776655",
        invoice_amount=Decimal("45000.00"),
        historical_avg_amount=Decimal("5000.00"),
        known_bank_hash="previous-verified-bank-hash",
        sender_domain="acme-supp1y.com",
        vendor_domain="acmesupply.com",
    )
    assert risk["risk_score"] >= 70
    assert risk["payment_frozen"] is True

    vapid = generate_local_vapid_keypair()
    assert vapid["algorithm"] == "ES256-P256" and len(vapid["public_key"]) > 40
    alert = build_vapid_alert_payload(
        title="Payment Frozen",
        body="Acme Supply bank details changed.",
        risk_score=risk["risk_score"],
        vapid_private_key_b64=vapid["private_key"],
        vapid_public_key_b64=vapid["public_key"],
    )
    assert alert["vapid_authorization"].startswith("vapid t=")


def test_n2_capex_scanner_and_macrs_pub946_dual_schedule() -> None:
    flagged = scan_capitalization_candidates(
        [
            {"id": "1", "description": "MacBook Pro Fleet", "amount": "6400.00"},
            {"id": "2", "description": "Printer Paper", "amount": "85.00"},
        ],
        threshold=Decimal("2500.00"),
    )
    assert len(flagged) == 1 and flagged[0]["suggested_recovery_period_years"] == 5

    sched = compute_dual_depreciation_schedule(
        asset_name="Production Server Rack",
        cost=Decimal("10000.00"),
        salvage_value=Decimal("0.00"),
        gaap_useful_life_years=5,
        macrs_recovery_period=5,
        section_179_amount=Decimal("2000.00"),
    )
    assert sched["total_gaap_depreciation"] == "10000.00"
    assert sched["total_tax_depreciation"] == "10000.00"
    # Year 1 tax = $2,000 Sec 179 + 20% of $8,000 ($1,600) = $3,600.00
    assert sched["schedule"][0]["tax_macrs_depreciation"] == "3600.00"


def test_n3_multi_sku_landed_cost_proration_conserves_every_cent() -> None:
    res = prorate_landed_cost(
        total_landed_cost=Decimal("100.00"),
        allocation_basis="weight",
        sku_lines=[
            {"sku": "SKU-A", "quantity": 10, "unit_cost": "12.00", "unit_weight": "1.0"},
            {"sku": "SKU-B", "quantity": 10, "unit_cost": "18.00", "unit_weight": "1.0"},
            {"sku": "SKU-C", "quantity": 10, "unit_cost": "25.00", "unit_weight": "1.0"},
        ],
    )
    assert res["exact_tie_out"] is True
    assert res["allocated_sum"] == "100.00"


def test_n4_native_xlsx_workpapers_archive_has_all_8_sheets() -> None:
    raw = build_workpapers_xlsx(
        business_name="Acme Corp",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        sheet_data={
            "Bank Rec": [
                {
                    "ref": "1010",
                    "description": "Operating Checking",
                    "gl_balance": "50000.00",
                    "subledger_balance": "50000.00",
                }
            ],
        },
    )
    with ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
        assert "xl/workbook.xml" in names
        assert "[Content_Types].xml" in names
        for idx in range(1, len(WORKPAPER_SHEETS) + 1):
            assert f"xl/worksheets/sheet{idx}.xml" in names
        wb_xml = zf.read("xl/workbook.xml").decode("utf-8")
        for title in WORKPAPER_SHEETS:
            assert title in wb_xml
