"""Autonomous AI Finance Office v2.0 REST API router (Phases I-N)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from ledgerpilot.accounting.macrs import (
    compute_dual_depreciation_schedule,
    scan_capitalization_candidates,
)
from ledgerpilot.banking.merchants import normalize_merchant
from ledgerpilot.banking.ofx_parser import ofx_to_rows
from ledgerpilot.connectors.payroll import parse_payroll_register_csv
from ledgerpilot.connectors.settlements import (
    unbundle_settlement_csv,
    unbundle_settlement_rows,
)
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.jobs.scheduler import (
    ScheduledJobKind,
    list_scheduled_jobs,
    upsert_scheduled_job,
)
from ledgerpilot.reports.flux import generate_flux_report
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize
from ledgerpilot.trade.deductions import evaluate_short_pay
from ledgerpilot.trade.fraud_sentinel import (
    build_vapid_alert_payload,
    evaluate_vendor_fraud_risk,
    generate_local_vapid_keypair,
    log_fraud_sentinel_event,
    register_push_subscription,
    validate_tax_id,
)
from ledgerpilot.workspace.accruals import (
    approve_accrual_candidate,
    list_accrual_candidates,
    run_day1_auto_reversals,
    scan_month_end_accruals,
)
from ledgerpilot.workspace.amortization import (
    create_amortization_schedule,
    list_amortization_schedules,
    run_monthly_amortization,
)
from ledgerpilot.workspace.auto_post import (
    AutoPostJobKind,
    list_auto_post_policies,
    upsert_auto_post_policy,
)
from ledgerpilot.workspace.chasing import (
    create_document_chase,
    list_document_chases,
    match_chased_receipt,
    verify_signed_upload_token,
)
from ledgerpilot.workspace.intercompany import (
    build_intercompany_mirror_entries,
    create_intercompany_link,
    list_intercompany_pairs,
    record_intercompany_pair,
)
from ledgerpilot.workspace.landed_cost import (
    prorate_landed_cost,
    save_landed_cost_allocation,
)
from ledgerpilot.workspace.workpapers import build_workpapers_xlsx

router = APIRouter(tags=["autonomous-v2"])

_ALL_ROLES = {"owner", "accountant", "bookkeeper", "reviewer"}
_WRITE_ROLES = {"owner", "accountant", "bookkeeper"}
_APPROVE_ROLES = {"owner", "accountant"}


class ScheduledJobIn(BaseModel):
    job_kind: ScheduledJobKind
    cron_expression: str | None = Field(default=None, max_length=64)
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True


class AutoPostPolicyIn(BaseModel):
    job_kind: AutoPostJobKind
    enabled: bool = False
    max_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))


class OfxImportIn(BaseModel):
    ofx_content: str = Field(min_length=10, max_length=5_000_000)


class MerchantNormalizeIn(BaseModel):
    raw_descriptor: str = Field(min_length=1, max_length=255)
    amount: Decimal = Decimal("-100.00")
    use_llm: bool = True


class ReviewDecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|reclassify|skip)$")
    category: str | None = Field(default=None, max_length=120)
    note: str = Field(default="", max_length=400)


class AccrualScanIn(BaseModel):
    period_end: date


class AccrualReverseIn(BaseModel):
    as_of: date


class AmortizationCreateIn(BaseModel):
    schedule_type: str = Field(pattern="^(prepaid_expense|deferred_revenue)$")
    total_amount: Decimal = Field(gt=Decimal("0.00"))
    start_date: date
    end_date: date
    currency: str = Field(default="USD", min_length=3, max_length=3)


class AmortizationRunIn(BaseModel):
    as_of: date


class FluxBalanceItem(BaseModel):
    account_code: str
    account_name: str
    account_type: str = "expense"
    balance: Decimal


class FluxRunIn(BaseModel):
    period_start: date
    period_end: date
    current_balances: list[FluxBalanceItem]
    prior_month_balances: list[FluxBalanceItem]
    prior_year_balances: list[FluxBalanceItem] | None = None


class DocumentChaseIn(BaseModel):
    recipient_email: str = Field(min_length=5, max_length=255)
    target_entity_type: str = Field(default="bank_transaction", max_length=40)
    target_entity_id: UUID | None = None
    channel: str = Field(default="email", pattern="^(email|slack|whatsapp)$")
    transaction_summary: str = Field(default="", max_length=300)


class PublicUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    extracted_vendor: str = Field(default="", max_length=160)
    extracted_amount: Decimal = Field(default=Decimal("0.00"))
    expected_vendor: str = Field(default="", max_length=160)
    expected_amount: Decimal = Field(default=Decimal("0.00"))


class ShortPayEvaluateIn(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=80)
    customer_name: str = Field(min_length=1, max_length=160)
    invoice_amount: Decimal = Field(gt=Decimal("0.00"))
    paid_amount: Decimal = Field(ge=Decimal("0.00"))
    invoice_date: date
    payment_date: date
    payment_terms: str = Field(default="Net 30", max_length=40)
    remittance_note: str = Field(default="", max_length=400)
    tone_profile: str = Field(default="friendly_firm", max_length=40)


class PayrollParseIn(BaseModel):
    provider: str = Field(default="gusto", max_length=32)
    csv_text: str = Field(min_length=10, max_length=2_000_000)
    bank_withdrawal_amount: Decimal | None = None


class SettlementUnbundleIn(BaseModel):
    provider: str = Field(default="paypal", max_length=32)
    csv_text: str | None = None
    rows: list[dict[str, Any]] | None = None


class IntercompanyLinkIn(BaseModel):
    counterparty_business_id: UUID
    due_from_account_id: UUID | None = None
    due_to_account_id: UUID | None = None


class IntercompanyMirrorIn(BaseModel):
    counterparty_business_id: UUID | None = None
    source_journal_id: UUID | None = None
    entity_a_name: str = Field(min_length=1, max_length=120)
    entity_b_name: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=Decimal("0.00"))
    currency: str = Field(default="USD", min_length=3, max_length=3)
    fx_rate_to_b: Decimal = Field(default=Decimal("1.000000"), gt=Decimal("0"))
    description: str = Field(default="Intercompany management fee", max_length=240)
    posting_date: date | None = None


class FraudCheckIn(BaseModel):
    vendor_name: str = Field(min_length=1, max_length=160)
    invoice_text: str = Field(min_length=1, max_length=20_000)
    invoice_amount: Decimal = Field(gt=Decimal("0.00"))
    historical_avg_amount: Decimal | None = None
    known_bank_hash: str | None = None
    sender_domain: str = ""
    vendor_domain: str = ""
    tax_id: str = ""
    country_code: str = "US"


class TaxIdValidateIn(BaseModel):
    tax_id: str = Field(min_length=1, max_length=64)
    country_code: str = Field(default="US", min_length=2, max_length=2)
    check_eu_vies_online: bool = False


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=500)
    p256dh: str = Field(min_length=8, max_length=255)
    auth_secret: str = Field(min_length=8, max_length=255)


class CapexScanIn(BaseModel):
    threshold: Decimal = Field(default=Decimal("2500.00"), gt=Decimal("0"))
    expense_lines: list[dict[str, Any]]


class MacrsScheduleIn(BaseModel):
    asset_name: str = Field(min_length=1, max_length=160)
    cost: Decimal = Field(gt=Decimal("0.00"))
    salvage_value: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    gaap_useful_life_years: int = Field(default=5, ge=1, le=40)
    macrs_recovery_period: int = Field(default=5)
    section_179_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    convention: str = Field(default="half_year", max_length=32)


class LandedCostIn(BaseModel):
    total_landed_cost: Decimal = Field(gt=Decimal("0.00"))
    allocation_basis: str = Field(default="weight", pattern="^(weight|volume|value|quantity)$")
    sku_lines: list[dict[str, Any]]
    save: bool = False


class WorkpapersRequestIn(BaseModel):
    business_name: str = Field(default="Workspace", max_length=160)
    period_start: date
    period_end: date
    sheet_data: dict[str, list[dict[str, Any]]] | None = None


@router.get("/businesses/{business_id}/autonomous/schedules")
def get_schedules(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return {"items": list_scheduled_jobs(db, business_id)}


@router.post("/businesses/{business_id}/autonomous/schedules")
def post_schedule(
    business_id: UUID,
    payload: ScheduledJobIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    job = upsert_scheduled_job(
        db,
        business_id=business_id,
        actor_id=identity.user.id,
        job_kind=payload.job_kind,
        cron_expr=payload.cron_expression,
        timezone=payload.timezone,
        enabled=payload.enabled,
    )
    db.commit()
    return job


@router.get("/businesses/{business_id}/autonomous/auto-post-policies")
def get_auto_post_policies(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return {"items": list_auto_post_policies(db, business_id)}


@router.post("/businesses/{business_id}/autonomous/auto-post-policies")
def post_auto_post_policy(
    business_id: UUID,
    payload: AutoPostPolicyIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    policy = upsert_auto_post_policy(
        db,
        identity,
        business_id,
        job_kind=payload.job_kind,
        enabled=payload.enabled,
        max_amount=payload.max_amount,
    )
    db.commit()
    return policy


@router.post("/businesses/{business_id}/bank-accounts/{account_id}/imports/ofx")
def import_ofx_statement(
    business_id: UUID,
    account_id: UUID,
    payload: OfxImportIn,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    rows = ofx_to_rows(payload.ofx_content)
    settings = request.app.state.settings
    enriched = []
    for r in rows:
        norm = normalize_merchant(
            db,
            business_id,
            r["description"],
            amount=Decimal(r["amount"]),
            settings=settings,
            use_llm=False,
        )
        enriched.append({**r, "merchant": norm})
    db.commit()
    return {
        "account_id": str(account_id),
        "imported_count": len(enriched),
        "transactions": enriched,
    }


@router.post("/businesses/{business_id}/banking/merchants/normalize")
def normalize_merchant_descriptor(
    business_id: UUID,
    payload: MerchantNormalizeIn,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    result = normalize_merchant(
        db,
        business_id,
        payload.raw_descriptor,
        amount=payload.amount,
        settings=request.app.state.settings,
        use_llm=payload.use_llm,
    )
    db.commit()
    return result


@router.get("/businesses/{business_id}/review-queue")
def get_review_queue(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    rows = (
        db.execute(
            text(
                """
                SELECT id, posted_date, description, amount, currency
                FROM app.bank_rows
                WHERE business_id = :bid AND status = 'unmatched'
                ORDER BY posted_date DESC
                LIMIT 50
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        amt = Decimal(str(r["amount"])).quantize(Decimal("0.01"))
        norm = normalize_merchant(
            None,
            business_id,
            str(r["description"]),
            amount=amt,
            settings=request.app.state.settings,
            use_llm=False,
        )
        items.append(
            {
                "bank_row_id": str(r["id"]),
                "posted_date": r["posted_date"].isoformat(),
                "raw_description": r["description"],
                "amount": str(amt),
                "currency": r["currency"],
                "merchant_name": norm["normalized_name"],
                "suggested_category": norm["business_category"],
                "confidence": norm["confidence"],
                "logo_url": norm["logo_url"],
                "journal_preview": norm["journal_preview"],
            }
        )
    return {"items": items, "count": len(items)}


@router.post("/businesses/{business_id}/review-queue/{bank_row_id}/decision")
def decide_review_item(
    business_id: UUID,
    bank_row_id: UUID,
    payload: ReviewDecisionIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    return {
        "bank_row_id": str(bank_row_id),
        "action": payload.action,
        "category": payload.category,
        "note": payload.note,
        "decided_by": str(identity.user.id),
        "status": "recorded",
    }


@router.get("/businesses/{business_id}/autonomous/accruals")
def get_accruals(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return {"items": list_accrual_candidates(db, business_id)}


@router.post("/businesses/{business_id}/autonomous/accruals/scan")
def post_accruals_scan(
    business_id: UUID,
    payload: AccrualScanIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    staged = scan_month_end_accruals(db, business_id, payload.period_end)
    db.commit()
    return {"staged_count": len(staged), "items": staged}


@router.post("/businesses/{business_id}/autonomous/accruals/{candidate_id}/approve")
def post_approve_accrual(
    business_id: UUID,
    candidate_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    res = approve_accrual_candidate(db, business_id, candidate_id)
    db.commit()
    return res


@router.post("/businesses/{business_id}/autonomous/accruals/reverse-due")
def post_reverse_due_accruals(
    business_id: UUID,
    payload: AccrualReverseIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    res = run_day1_auto_reversals(db, business_id, payload.as_of)
    db.commit()
    return res


@router.get("/businesses/{business_id}/autonomous/amortization")
def get_amortization(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return {"items": list_amortization_schedules(db, business_id)}


@router.post("/businesses/{business_id}/autonomous/amortization")
def post_amortization(
    business_id: UUID,
    payload: AmortizationCreateIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    res = create_amortization_schedule(
        db,
        business_id,
        schedule_type=payload.schedule_type,
        total_amount=payload.total_amount,
        start_date=payload.start_date,
        end_date=payload.end_date,
        currency=payload.currency,
    )
    db.commit()
    return res


@router.post("/businesses/{business_id}/autonomous/amortization/run")
def post_run_amortization(
    business_id: UUID,
    payload: AmortizationRunIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    res = run_monthly_amortization(db, business_id, payload.as_of)
    db.commit()
    return res


@router.post("/businesses/{business_id}/reports/flux-analysis")
def post_flux_analysis(
    business_id: UUID,
    payload: FluxRunIn,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return generate_flux_report(
        business_id=business_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        current_balances=[b.model_dump() for b in payload.current_balances],
        prior_month_balances=[b.model_dump() for b in payload.prior_month_balances],
        prior_year_balances=(
            [b.model_dump() for b in payload.prior_year_balances]
            if payload.prior_year_balances is not None
            else None
        ),
        settings=request.app.state.settings,
    )


@router.get("/businesses/{business_id}/autonomous/chasing")
def get_chases(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return {"items": list_document_chases(db, business_id)}


@router.post("/businesses/{business_id}/autonomous/chasing")
def post_chase(
    business_id: UUID,
    payload: DocumentChaseIn,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    res = create_document_chase(
        db,
        request.app.state.settings,
        business_id,
        recipient_email=payload.recipient_email,
        target_entity_type=payload.target_entity_type,
        target_entity_id=payload.target_entity_id,
        channel=payload.channel,
        transaction_summary=payload.transaction_summary,
    )
    db.commit()
    return res


@router.post("/public-uploads/{token}")
def post_public_chase_upload(
    token: str,
    payload: PublicUploadIn,
    request: Request,
) -> dict[str, Any]:
    """Single-use signed 7-day upload endpoint for chased receipts (no login required)."""
    bid, cid = verify_signed_upload_token(request.app.state.settings, token)
    match = match_chased_receipt(
        None,
        bid,
        expected_amount=payload.expected_amount,
        extracted_amount=payload.extracted_amount,
        expected_vendor=payload.expected_vendor,
        extracted_vendor=payload.extracted_vendor,
    )
    return {
        "chase_event_id": str(cid),
        "business_id": str(bid),
        "filename": payload.filename,
        "status": "matched" if match["auto_linked"] else "received",
        **match,
    }


@router.post("/businesses/{business_id}/trade/deductions/evaluate")
def post_evaluate_short_pay(
    business_id: UUID,
    payload: ShortPayEvaluateIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return evaluate_short_pay(
        invoice_number=payload.invoice_number,
        customer_name=payload.customer_name,
        invoice_amount=payload.invoice_amount,
        paid_amount=payload.paid_amount,
        invoice_date=payload.invoice_date,
        payment_date=payload.payment_date,
        payment_terms=payload.payment_terms,
        remittance_note=payload.remittance_note,
        tone_profile=payload.tone_profile,
    )


@router.post("/businesses/{business_id}/connectors/payroll/parse")
def post_parse_payroll(
    business_id: UUID,
    payload: PayrollParseIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    return parse_payroll_register_csv(
        payload.csv_text,
        provider=payload.provider,
        bank_withdrawal_amount=payload.bank_withdrawal_amount,
    )


@router.post("/businesses/{business_id}/connectors/settlements/unbundle")
def post_unbundle_settlement(
    business_id: UUID,
    payload: SettlementUnbundleIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    if payload.csv_text:
        return unbundle_settlement_csv(payload.provider, payload.csv_text)
    if payload.rows is not None:
        return unbundle_settlement_rows(provider=payload.provider, rows=payload.rows)
    raise DomainError("ERR_VALIDATION", "Provide either csv_text or rows.", 422)


@router.post("/businesses/{business_id}/intercompany/links")
def post_intercompany_link(
    business_id: UUID,
    payload: IntercompanyLinkIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    res = create_intercompany_link(
        db,
        user_id=identity.user.id,
        business_id=business_id,
        counterparty_business_id=payload.counterparty_business_id,
        due_from_account_id=payload.due_from_account_id,
        due_to_account_id=payload.due_to_account_id,
    )
    db.commit()
    return res


@router.get("/businesses/{business_id}/intercompany/pairs")
def get_intercompany_pairs(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return {"items": list_intercompany_pairs(db, business_id)}


@router.post("/businesses/{business_id}/intercompany/mirror")
def post_intercompany_mirror(
    business_id: UUID,
    payload: IntercompanyMirrorIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _APPROVE_ROLES)
    mirror = build_intercompany_mirror_entries(
        entity_a_name=payload.entity_a_name,
        entity_b_name=payload.entity_b_name,
        amount=payload.amount,
        currency=payload.currency,
        fx_rate_to_b=payload.fx_rate_to_b,
        description=payload.description,
        posting_date=payload.posting_date,
    )
    if payload.counterparty_business_id:
        pair = record_intercompany_pair(
            db,
            business_id=business_id,
            counterparty_business_id=payload.counterparty_business_id,
            source_journal_id=payload.source_journal_id or uuid4(),
            amount=payload.amount,
            currency=payload.currency,
        )
        db.commit()
        mirror["pair"] = pair
    return mirror


@router.post("/businesses/{business_id}/fraud-sentinel/evaluate")
def post_fraud_evaluate(
    business_id: UUID,
    payload: FraudCheckIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    ev = evaluate_vendor_fraud_risk(
        vendor_name=payload.vendor_name,
        invoice_text=payload.invoice_text,
        invoice_amount=payload.invoice_amount,
        historical_avg_amount=payload.historical_avg_amount,
        known_bank_hash=payload.known_bank_hash,
        sender_domain=payload.sender_domain,
        vendor_domain=payload.vendor_domain,
        tax_id=payload.tax_id,
        country_code=payload.country_code,
    )
    logged = log_fraud_sentinel_event(db, business_id, evaluation=ev)
    db.commit()
    return logged


@router.post("/businesses/{business_id}/fraud-sentinel/validate-tax-id")
def post_validate_tax_id(
    business_id: UUID,
    payload: TaxIdValidateIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return validate_tax_id(
        payload.tax_id,
        country_code=payload.country_code,
        check_eu_vies_online=payload.check_eu_vies_online,
    )


@router.post("/businesses/{business_id}/notifications/vapid/generate")
def post_generate_vapid(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner"})
    keys = generate_local_vapid_keypair()
    sample = build_vapid_alert_payload(
        title="LedgerPilot Owner Alert Test",
        body="Local Web Push VAPID keys generated and verified.",
        risk_score=85,
        vapid_private_key_b64=keys["private_key"],
        vapid_public_key_b64=keys["public_key"],
    )
    return {
        "algorithm": keys["algorithm"],
        "public_key": keys["public_key"],
        "sample_alert_ready": True,
        "sample_notification": sample["notification"],
    }


@router.post("/businesses/{business_id}/notifications/push-subscriptions")
def post_push_subscription(
    business_id: UUID,
    payload: PushSubscribeIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    res = register_push_subscription(
        db,
        business_id=business_id,
        user_id=identity.user.id,
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth_secret=payload.auth_secret,
    )
    db.commit()
    return res


@router.post("/businesses/{business_id}/fixed-assets/capex-scan")
def post_capex_scan(
    business_id: UUID,
    payload: CapexScanIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    flagged = scan_capitalization_candidates(payload.expense_lines, threshold=payload.threshold)
    return {"flagged_count": len(flagged), "items": flagged}


@router.post("/businesses/{business_id}/fixed-assets/macrs-schedule")
def post_macrs_schedule(
    business_id: UUID,
    payload: MacrsScheduleIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _ALL_ROLES)
    return compute_dual_depreciation_schedule(
        asset_name=payload.asset_name,
        cost=payload.cost,
        salvage_value=payload.salvage_value,
        gaap_useful_life_years=payload.gaap_useful_life_years,
        macrs_recovery_period=payload.macrs_recovery_period,
        section_179_amount=payload.section_179_amount,
        convention=payload.convention,
    )


@router.post("/businesses/{business_id}/inventory/landed-cost/prorate")
def post_prorate_landed_cost(
    business_id: UUID,
    payload: LandedCostIn,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, _WRITE_ROLES)
    res = prorate_landed_cost(
        total_landed_cost=payload.total_landed_cost,
        allocation_basis=payload.allocation_basis,
        sku_lines=payload.sku_lines,
    )
    if payload.save:
        res = save_landed_cost_allocation(db, business_id, result=res)
        db.commit()
    return res


@router.post("/businesses/{business_id}/exports/workpapers.xlsx")
def post_export_workpapers(
    business_id: UUID,
    payload: WorkpapersRequestIn,
    identity: Identity,
    db: DB,
) -> Response:
    authorize(db, identity, business_id, _ALL_ROLES)
    xlsx_bytes = build_workpapers_xlsx(
        business_name=payload.business_name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        sheet_data=payload.sheet_data,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="workpapers.xlsx"'},
    )
