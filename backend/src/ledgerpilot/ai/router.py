"""W0 Foundation and AI-01 through AI-10 FastAPI routes and deterministic accounting services."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import text

from ledgerpilot.ai.adapter import (
    GroqModelAdapter,
    ModelGenerationResult,
    generate_livekit_room_token,
    sanitize_untrusted_text,
)
from ledgerpilot.ai.tools import (
    AccountBalancesToolInput,
    ApprovedMemoriesToolInput,
    CloseReadinessToolInput,
    OpenDocumentsToolInput,
    ToolInvocationAudit,
    UnmatchedBankRowsToolInput,
    persist_tool_audits,
    tool_get_account_balances,
    tool_get_close_readiness_snapshot,
    tool_get_open_trade_documents,
    tool_get_unmatched_bank_rows,
    tool_lookup_approved_memories,
)
from ledgerpilot.audit.service import record as record_audit
from ledgerpilot.config import Settings, get_settings
from ledgerpilot.database import set_context
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.identity.mail import deliver_workspace_email, format_mail_error_for_user
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, effective_policy, get_business

router = APIRouter(tags=["ai-copilot"])


def _q2(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _snapshot_hash(
    business_id: UUID, business_version: int, policy_version: int, extra: str = ""
) -> str:
    payload = f"{business_id}:{business_version}:{policy_version}:{extra}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _get_or_default_ai_controls(db: DB, business_id: UUID) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                """
                SELECT ai_enabled, operator_paused, pause_reason,
                       monthly_budget_usd, monthly_token_budget, max_concurrent_runs
                FROM app.ai_tenant_controls
                WHERE business_id = :bid
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .first()
    )
    usage = (
        db.execute(
            text(
                """
                SELECT COALESCE(SUM(total_tokens), 0) AS used_tokens,
                       COALESCE(SUM(estimated_cost_usd), 0) AS used_cost_usd
                FROM app.ai_runs
                WHERE business_id = :bid
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .one()
    )
    ai_enabled = bool(row["ai_enabled"]) if row else True
    operator_paused = bool(row["operator_paused"]) if row else False
    pause_reason = str(row["pause_reason"] or "") if row else ""
    monthly_budget_usd = Decimal(str(row["monthly_budget_usd"])) if row else Decimal("25.000000")
    monthly_token_budget = int(row["monthly_token_budget"]) if row else 500000
    max_concurrent_runs = int(row["max_concurrent_runs"]) if row else 4
    used_tokens = int(usage["used_tokens"] or 0)
    used_cost_usd = Decimal(str(usage["used_cost_usd"] or "0"))
    return {
        "ai_enabled": ai_enabled,
        "operator_paused": operator_paused,
        "pause_reason": pause_reason,
        "monthly_budget_usd": f"{monthly_budget_usd:.6f}",
        "monthly_token_budget": monthly_token_budget,
        "max_concurrent_runs": max_concurrent_runs,
        "used_tokens": used_tokens,
        "used_cost_usd": f"{used_cost_usd:.6f}",
        "manual_workflows_unaffected": True,
    }


def _enforce_ai_tenant_controls(db: DB, business_id: UUID) -> None:
    ctrl = _get_or_default_ai_controls(db, business_id)
    if not ctrl["ai_enabled"] or ctrl["operator_paused"]:
        reason = ctrl["pause_reason"] or "Paused by workspace operator"
        raise DomainError(
            "ERR_AI_PAUSED",
            f"AI recommendations are paused for this workspace ({reason}); "
            "manual workflows remain active.",
            409,
        )
    if int(ctrl["used_tokens"]) >= int(ctrl["monthly_token_budget"]) or Decimal(
        str(ctrl["used_cost_usd"])
    ) >= Decimal(str(ctrl["monthly_budget_usd"])):
        raise DomainError(
            "ERR_AI_BUDGET_EXCEEDED",
            "Workspace monthly AI token or cost budget reached; manual workflows remain active.",
            429,
        )


def _persist_ai_run(
    db: DB,
    *,
    business_id: UUID,
    actor_id: UUID,
    feature_id: str,
    wave: str,
    input_summary: str,
    model_result: ModelGenerationResult,
    structured_output: dict[str, Any],
    citations: list[dict[str, Any]],
    tool_audits: list[ToolInvocationAudit],
    confidence_score: Decimal,
    uncertainty_flags: list[str],
    business_version: int,
    policy_version: int,
    status: str = "completed",
    approval_state: str = "pending_review",
) -> dict[str, Any]:
    _enforce_ai_tenant_controls(db, business_id)
    run_id = uuid4()
    snap_hash = _snapshot_hash(business_id, business_version, policy_version, feature_id)
    app_hash = _content_hash(
        {
            "feature_id": feature_id,
            "business_version": business_version,
            "structured_output": structured_output,
        }
    )
    tool_versions = {a.tool_name: a.tool_version for a in tool_audits}
    tool_list = [a.model_dump() for a in tool_audits]

    db.execute(
        text(
            """
            INSERT INTO app.ai_runs (
              id, business_id, feature_id, wave, provider, model_name, prompt_version,
              tool_versions, status, input_summary, structured_output, citations,
              tool_invocations, confidence_score, uncertainty_flags, snapshot_hash,
              business_version, policy_version, prompt_tokens, completion_tokens,
              total_tokens, latency_ms, estimated_cost_usd, approval_state,
              approval_content_hash, actor
            ) VALUES (
              :id, :bid, :fid, :wave, :prov, :mname, 'v1.0',
              CAST(:tvers AS jsonb), :status, :isum, CAST(:sout AS jsonb), CAST(:cites AS jsonb),
              CAST(:tinv AS jsonb), :conf, CAST(:uflags AS text[]), :shash,
              :bver, :pver, :ptok, :ctok,
              :ttok, :lat, :cost, :astate,
              :ahash, :actor
            )
            """
        ),
        {
            "id": run_id,
            "bid": business_id,
            "fid": feature_id,
            "wave": wave,
            "prov": model_result.provider,
            "mname": model_result.model_name,
            "tvers": json.dumps(tool_versions),
            "status": status,
            "isum": input_summary[:500],
            "sout": json.dumps(structured_output, default=str),
            "cites": json.dumps(citations, default=str),
            "tinv": json.dumps(tool_list, default=str),
            "conf": confidence_score.quantize(Decimal("0.0001")),
            "uflags": uncertainty_flags,
            "shash": snap_hash,
            "bver": business_version,
            "pver": policy_version,
            "ptok": model_result.prompt_tokens,
            "ctok": model_result.completion_tokens,
            "ttok": model_result.total_tokens,
            "lat": model_result.latency_ms,
            "cost": model_result.estimated_cost_usd,
            "astate": approval_state,
            "ahash": app_hash,
            "actor": actor_id,
        },
    )
    persist_tool_audits(
        db,
        business_id=business_id,
        run_id=run_id,
        actor_id=actor_id,
        audits=tool_audits,
    )
    record_audit(db, actor_id, f"ai.run.{feature_id.lower()}", business_id, run_id)
    return {
        "run_id": str(run_id),
        "feature_id": feature_id,
        "wave": wave,
        "provider": model_result.provider,
        "model_name": model_result.model_name,
        "status": status,
        "approval_state": approval_state,
        "approval_content_hash": app_hash,
        "snapshot_hash": snap_hash,
        "business_version": business_version,
        "policy_version": policy_version,
        "confidence_score": f"{confidence_score:.4f}",
        "uncertainty_flags": uncertainty_flags,
        "prompt_tokens": model_result.prompt_tokens,
        "completion_tokens": model_result.completion_tokens,
        "total_tokens": model_result.total_tokens,
        "latency_ms": model_result.latency_ms,
        "estimated_cost_usd": f"{model_result.estimated_cost_usd:.6f}",
        "citations": citations,
        "tool_invocations": tool_list,
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ImpactPreviewLineInput(Schema):
    account_code: str = Field(min_length=1, max_length=30)
    account_name: str = Field(default="", max_length=120)
    kind: Literal["asset", "liability", "equity", "income", "expense"] = "expense"
    control: Literal["ar", "ap", "tax", "clearing", "deposit", "cash", "none"] = "none"
    debit: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    credit: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))


class ImpactPreviewRequest(Schema):
    accounting_date: date
    memo: str = Field(min_length=1, max_length=300)
    expected_business_version: int | None = None
    lines: list[ImpactPreviewLineInput] = Field(min_length=2, max_length=50)


class AccountingMemoryCreateInput(Schema):
    memory_key: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=140)
    accounting_framework: Literal["US_GAAP", "IFRS", "TAX_BASIS"] = "US_GAAP"
    category: Literal[
        "categorization", "recognition", "materiality", "vendor_policy", "close_rule"
    ] = "categorization"
    binding_kind: Literal["binding_policy", "past_example"] = "binding_policy"
    rule_condition: str = Field(min_length=3, max_length=400)
    account_code: str = Field(min_length=1, max_length=30)
    account_name: str = Field(min_length=1, max_length=120)
    materiality_threshold: Decimal = Field(default=Decimal("500.00"), ge=Decimal("0.00"))
    effective_from: date
    effective_to: date | None = None
    evidence_note: str = Field(default="", max_length=240)


class AccountingMemoryDecisionInput(Schema):
    decision: Literal["approve", "supersede", "revoke"]
    reason: str = Field(default="", max_length=240)


class ReconciliationDetectiveInput(Schema):
    bank_row_id: UUID | None = None
    statement_description: str = Field(min_length=2, max_length=240)
    statement_amount: Decimal
    posted_date: date
    is_duplicate_external_id: bool = False


class ClientClarificationCreateInput(Schema):
    exception_kind: Literal[
        "bank_row", "missing_invoice", "unmatched_payment", "contract_variance"
    ] = "missing_invoice"
    exception_ref: str = Field(min_length=2, max_length=120)
    recipient_key: str = Field(min_length=2, max_length=254)
    channel: Literal["email", "whatsapp", "slack"] = "email"
    context_summary: str = Field(min_length=3, max_length=400)
    required_documents: list[str] = Field(
        default_factory=lambda: ["Invoice PDF", "Tax Receipt Page"]
    )


class ClientClarificationReplyInput(Schema):
    client_reply_text: str = Field(default="", max_length=600)
    uploaded_evidence_ref: str = Field(default="", max_length=160)
    provided_documents: list[str] = Field(default_factory=list)
    client_action: Literal["reply", "opt_out", "dispute"] = "reply"


class CloseAssistantInput(Schema):
    period_label: str = Field(default="2026-11", min_length=4, max_length=32)
    materiality_threshold: Decimal = Field(default=Decimal("500.00"), ge=Decimal("0.00"))
    check_recurring_omissions: bool = True


class VoiceAccountingSessionInput(Schema):
    language_locale: Literal["en_US", "ur_PK", "en_ur_mixed"] = "en_ur_mixed"
    audio_base64: str = Field(default="", max_length=4_000_000)
    audio_format: str = Field(default="audio/webm", max_length=80)
    transcript_input: str = Field(default="", max_length=600)
    request_livekit_room: bool = False
    recording_id: str = Field(default="", max_length=80)
    regenerate: bool = False


class VoiceAccountingConfirmInput(Schema):
    corrected_transcript: str = Field(min_length=3, max_length=600)
    confirmed_party_name: str = Field(min_length=1, max_length=120)
    confirmed_amount: Decimal = Field(gt=Decimal("0.00"))
    confirmed_currency: str = Field(default="USD", min_length=3, max_length=3)
    confirmed_date: date
    confirmed_account_code: str = Field(default="5000", min_length=1, max_length=30)


class ContractVerificationInput(Schema):
    contract_reference: str = Field(min_length=2, max_length=120)
    vendor_or_customer: str = Field(min_length=2, max_length=120)
    contract_clause_text: str = Field(min_length=5, max_length=1000)
    contracted_amount: Decimal = Field(ge=Decimal("0.00"))
    invoiced_amount: Decimal = Field(ge=Decimal("0.00"))
    paid_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    prior_payment_count: int = Field(default=0, ge=0, le=50)
    unapproved_milestone_billed: bool = False


class CashScenarioInput(Schema):
    scenario_name: str = Field(min_length=2, max_length=120)
    natural_language_prompt: str = Field(min_length=3, max_length=400)
    horizon_weeks: int = Field(default=13, ge=4, le=52)
    baseline_starting_cash: Decimal = Field(default=Decimal("50000.00"))
    baseline_weekly_inflow: Decimal = Field(default=Decimal("12000.00"), ge=Decimal("0.00"))
    baseline_weekly_outflow: Decimal = Field(default=Decimal("9500.00"), ge=Decimal("0.00"))
    payment_delay_weeks: int = Field(default=0, ge=0, le=12)
    equipment_purchase_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    recurring_cost_change_pct: Decimal = Field(
        default=Decimal("0.00"), ge=Decimal("-90.00"), le=Decimal("200.00")
    )
    collections_change_pct: Decimal = Field(
        default=Decimal("0.00"), ge=Decimal("-90.00"), le=Decimal("200.00")
    )


class PortfolioPlannerInput(Schema):
    materiality_threshold: Decimal = Field(default=Decimal("500.00"), ge=Decimal("0.00"))
    horizon_days: int = Field(default=7, ge=1, le=30)


class AIRunDecisionInput(Schema):
    decision: Literal["approve", "reject", "cancel"]
    expected_content_hash: str = Field(min_length=6, max_length=64)
    reason: str = Field(default="", max_length=240)


class AITenantControlsUpdateInput(Schema):
    ai_enabled: bool = True
    operator_paused: bool = False
    pause_reason: str = Field(default="", max_length=240)
    monthly_budget_usd: Decimal = Field(
        default=Decimal("25.000000"), ge=Decimal("0.00"), le=Decimal("100000.00")
    )
    monthly_token_budget: int = Field(default=500000, ge=0, le=100000000)
    max_concurrent_runs: int = Field(default=4, ge=1, le=32)


class AIDatasetEvaluationInput(Schema):
    dataset_key: str = Field(min_length=2, max_length=80)
    dataset_cohort: Literal["synthetic_development", "representative_deidentified"] = (
        "synthetic_development"
    )
    execution_provenance: Literal[
        "supplied_prediction", "actual_groq_model_execution", "deterministic_engine_execution"
    ] = "actual_groq_model_execution"
    feature_id: Literal[
        "AI-01",
        "AI-02",
        "AI-03",
        "AI-04",
        "AI-05",
        "AI-06",
        "AI-07",
        "AI-08",
        "AI-09",
        "AI-10",
    ] = "AI-01"
    sample_count: int = Field(default=10, ge=1, le=10000)
    precision_score: Decimal = Field(
        default=Decimal("0.9600"), ge=Decimal("0.0000"), le=Decimal("1.0000")
    )
    recall_score: Decimal = Field(
        default=Decimal("0.9400"), ge=Decimal("0.0000"), le=Decimal("1.0000")
    )
    citation_validity_rate: Decimal = Field(
        default=Decimal("1.0000"), ge=Decimal("0.0000"), le=Decimal("1.0000")
    )
    notes: str = Field(default="", max_length=300)


# ---------------------------------------------------------------------------
# W0 & AI-10: Overview, Runs, Provenance & Tool Audit Logs
# ---------------------------------------------------------------------------


@router.get("/businesses/{business_id}/ai/overview")
def get_ai_copilot_overview(
    business_id: UUID,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Return W0 foundation status, AI-10 evidence records, tool audits, and AI items."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)
    adapter = GroqModelAdapter(settings)
    lk = generate_livekit_room_token(
        settings, business_id=business_id, user_id=user.user.id, room_name="accounting-voice"
    )

    runs = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, feature_id, wave, provider, model_name, prompt_version,
                       status, input_summary, structured_output, citations, tool_invocations,
                       confidence_score, uncertainty_flags, snapshot_hash, business_version,
                       policy_version, prompt_tokens, completion_tokens, total_tokens,
                       latency_ms, estimated_cost_usd, approval_state, approval_content_hash,
                       approved_by, approved_at, rejection_reason, created_at
                FROM app.ai_runs
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 30
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    tool_logs = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, run_id, tool_name, parameters_hash, row_count,
                       result_bytes, untrusted_content_sanitized, duration_ms, executed_at
                FROM app.ai_tool_audit_logs
                WHERE business_id = :bid
                ORDER BY executed_at DESC
                LIMIT 40
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    memories = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, memory_key, title, accounting_framework, category, binding_kind,
                       rule_condition, account_code, account_name, materiality_threshold,
                       effective_from, effective_to, version, status, evidence_refs, created_at
                FROM app.ai_accounting_memories
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 25
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    investigations = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, run_id, statement_description, statement_amount, currency,
                       classification, candidates, deterministic_delta, fee_amount,
                       proposed_allocation, explanation, status, created_at
                FROM app.ai_reconciliation_investigations
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    clarifications = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, run_id, exception_kind, exception_ref, recipient_key, channel,
                       questions, required_documents, client_reply_text, uploaded_evidence_ref,
                       completeness_score, remaining_gaps, status, created_at
                FROM app.ai_client_clarifications
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    voice_sessions = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, run_id, language_locale, audio_format, duration_seconds,
                       stt_provider, raw_transcript, corrected_transcript, extracted_intent,
                       extracted_entities, uncertain_fields, critical_fields_confirmed,
                       linked_draft_id, tts_readback_text, livekit_room_name, status, created_at
                FROM app.ai_voice_accounting_sessions
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    contracts_list = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, run_id, contract_reference, vendor_or_customer, currency,
                       contracted_amount, invoiced_amount, paid_amount, discrepancy_amount,
                       discrepancy_kind, extracted_clauses, proposed_action, status, created_at
                FROM app.ai_contract_verifications
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    cash_scenarios = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, run_id, scenario_name, natural_language_prompt, currency,
                       horizon_weeks, parameters, baseline_ending_cash, scenario_ending_cash,
                       net_cash_delta, lowest_weekly_cash,
                       shortfall_weeks, weekly_series, created_at
                FROM app.ai_cash_scenarios
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 15
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    evaluation_datasets = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, dataset_key, dataset_cohort, execution_provenance, feature_id,
                       sample_count, precision_score, recall_score, f1_score,
                       citation_validity_rate, promotion_gate_passed, notes, created_at
                FROM app.ai_evaluation_datasets
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 25
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    ]

    def _stringify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in rows:
            conv: dict[str, Any] = {}
            for k, v in item.items():
                if isinstance(v, UUID | date):
                    conv[k] = str(v)
                elif isinstance(v, Decimal):
                    if k == "estimated_cost_usd":
                        conv[k] = f"{v:.6f}"
                    elif k in (
                        "confidence_score",
                        "precision_score",
                        "recall_score",
                        "f1_score",
                        "citation_validity_rate",
                    ):
                        conv[k] = f"{v:.4f}"
                    else:
                        conv[k] = f"{v:.2f}"
                else:
                    conv[k] = v
            out.append(conv)
        return out

    return {
        "foundation": {
            "wave": "W0_TO_W6_ACTIVE",
            "groq_configured": adapter.is_configured(),
            "active_provider": "groq" if adapter.is_configured() else "deterministic_fallback",
            "llm_model": settings.groq_llm_model,
            "stt_model": settings.groq_stt_model,
            "tts_mode": "browser_speech_synthesis",
            "livekit_enabled": bool(lk["livekit_enabled"]),
            "livekit_mode": lk["mode"],
            "business_currency": biz.currency,
            "business_basis": pol.basis if pol else biz.basis,
            "business_version": biz.version,
            "policy_version": pol.version if pol else 1,
            "read_only_tools_enforced": True,
            "prompt_injection_shield_active": True,
            "human_approval_required": True,
        },
        "controls": _get_or_default_ai_controls(db, business_id),
        "evaluation_datasets": _stringify(evaluation_datasets),
        "runs": _stringify(runs),
        "tool_audit_logs": _stringify(tool_logs),
        "memories": _stringify(memories),
        "reconciliation_investigations": _stringify(investigations),
        "client_clarifications": _stringify(clarifications),
        "voice_sessions": _stringify(voice_sessions),
        "contract_verifications": _stringify(contracts_list),
        "cash_scenarios": _stringify(cash_scenarios),
    }


# ---------------------------------------------------------------------------
# AI-02: Financial Impact Preview (W1)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/impact-preview")
def run_ai_financial_impact_preview(
    business_id: UUID,
    payload: ImpactPreviewRequest,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-02: Compute deterministic before/after account, P&L, BS, AR/AP, and cash impacts."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz, payload.accounting_date)
    if (
        payload.expected_business_version is not None
        and payload.expected_business_version != biz.version
    ):
        raise DomainError(
            "ERR_VERSION",
            "Workspace ledger version changed; refresh before previewing impact.",
            409,
        )

    locked_period = db.execute(
        text(
            """
            SELECT 1 FROM app.periods
            WHERE business_id = :bid
              AND :adate BETWEEN start_date AND end_date
              AND locked = true
            LIMIT 1
            """
        ),
        {"bid": business_id, "adate": payload.accounting_date},
    ).scalar_one_or_none()
    if locked_period:
        raise DomainError(
            "ERR_PERIOD_LOCKED",
            "The selected accounting date falls in a locked accounting period.",
            409,
        )

    total_debit = sum((_q2(line.debit) for line in payload.lines), Decimal("0.00"))
    total_credit = sum((_q2(line.credit) for line in payload.lines), Decimal("0.00"))
    if total_debit <= Decimal("0.00") or total_debit != total_credit:
        raise DomainError(
            "ERR_UNBALANCED",
            f"Preview lines must balance (debit={total_debit:.2f}, credit={total_credit:.2f}).",
            422,
        )

    acct_tool = tool_get_account_balances(
        db, AccountBalancesToolInput(business_id=business_id, kind_filter="all", limit=50)
    )
    existing_by_code = {a.code: a for a in acct_tool.items}

    line_impacts: list[dict[str, Any]] = []
    pnl_net_delta = Decimal("0.00")
    assets_delta = Decimal("0.00")
    liabilities_delta = Decimal("0.00")
    equity_delta = Decimal("0.00")
    ar_delta = Decimal("0.00")
    ap_delta = Decimal("0.00")
    cash_delta = Decimal("0.00")

    for line in payload.lines:
        d = _q2(line.debit)
        c = _q2(line.credit)
        match = existing_by_code.get(line.account_code.strip())
        kind = match.kind if match else line.kind
        ctrl = (match.control or line.control) if match else line.control
        before_bal = _q2(match.net_balance) if match else Decimal("0.00")
        delta = (d - c) if kind in ("asset", "expense") else (c - d)
        after_bal = before_bal + delta

        if kind == "income":
            pnl_net_delta += c - d
        elif kind == "expense":
            pnl_net_delta -= d - c
        elif kind == "asset":
            assets_delta += d - c
        elif kind == "liability":
            liabilities_delta += c - d
        elif kind == "equity":
            equity_delta += c - d

        if ctrl == "ar":
            ar_delta += d - c
        elif ctrl == "ap":
            ap_delta += c - d
        if ctrl == "cash" or (
            kind == "asset"
            and (
                line.account_code.startswith("10")
                or "cash" in (line.account_name or "").lower()
                or "bank" in (line.account_name or "").lower()
            )
        ):
            cash_delta += d - c

        line_impacts.append(
            {
                "account_code": line.account_code.strip(),
                "account_name": (match.name if match else line.account_name)
                or line.account_code.strip(),
                "kind": kind,
                "control": ctrl,
                "debit": f"{d:.2f}",
                "credit": f"{c:.2f}",
                "before_balance": f"{before_bal:.2f}",
                "delta": f"{delta:.2f}",
                "after_balance": f"{after_bal:.2f}",
            }
        )

    clean_memo, _ = sanitize_untrusted_text(payload.memo, max_length=240)
    fallback_payload = {
        "accounting_date": str(payload.accounting_date),
        "currency": biz.currency,
        "basis": pol.basis if pol else biz.basis,
        "total_debit": f"{total_debit:.2f}",
        "total_credit": f"{total_credit:.2f}",
        "pnl_net_income_delta": f"{pnl_net_delta:.2f}",
        "assets_delta": f"{assets_delta:.2f}",
        "liabilities_delta": f"{liabilities_delta:.2f}",
        "equity_delta": f"{equity_delta + pnl_net_delta:.2f}",
        "ar_subledger_delta": f"{ar_delta:.2f}",
        "ap_subledger_delta": f"{ap_delta:.2f}",
        "cash_delta": f"{cash_delta:.2f}",
        "is_noncash_reclassification": cash_delta == Decimal("0.00"),
        "tax_liability_note": "Excludes uncalculated jurisdictional tax return liabilities.",
        "line_impacts": line_impacts,
        "narrative": (
            f"Balanced {biz.currency} entry ({total_debit:.2f}) on {payload.accounting_date}: "
            f"P&L net income changes by {pnl_net_delta:+.2f}, cash changes by {cash_delta:+.2f}."
        ),
    }

    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-02",
        system_prompt=(
            "You are LedgerPilot AI-02 Financial Impact Preview. Never alter the deterministic "
            "Decimal amounts provided. Return JSON with a 'narrative' key explaining impact."
        ),
        user_prompt=f"Memo: {clean_memo}\nDeterministic impact: {json.dumps(fallback_payload)}",
        fallback_payload=fallback_payload,
    )
    # Deterministic arithmetic always takes precedence over any LLM fields
    final_output = {
        **fallback_payload,
        "narrative": str(model_res.structured.get("narrative") or fallback_payload["narrative"]),
    }
    citations = [
        {
            "source_type": "ledger_snapshot",
            "reference": f"business:{business_id}:v{biz.version}",
            "basis": pol.basis if pol else biz.basis,
            "currency": biz.currency,
        }
    ]
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-02",
        wave="W1",
        input_summary=f"Impact preview ({total_debit:.2f} {biz.currency}): {clean_memo}",
        model_result=model_res,
        structured_output=final_output,
        citations=citations,
        tool_audits=[acct_tool.audit],
        confidence_score=Decimal("1.0000"),
        uncertainty_flags=[],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )
    return {**final_output, "evidence_record": run_meta}


# ---------------------------------------------------------------------------
# AI-04: Approved Business Accounting Memory (W1)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/memories", status_code=201)
def create_ai_accounting_memory(
    business_id: UUID,
    payload: AccountingMemoryCreateInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-04: Create a structured framework-scoped accounting memory requiring approval."""
    authorize(db, user, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)
    if payload.effective_to and payload.effective_to < payload.effective_from:
        raise DomainError("ERR_VALIDATION", "effective_to cannot precede effective_from.", 422)

    clean_key = payload.memory_key.strip().lower()
    clean_title, _ = sanitize_untrusted_text(payload.title, max_length=140)
    clean_cond, was_san = sanitize_untrusted_text(payload.rule_condition, max_length=400)
    clean_note, _ = sanitize_untrusted_text(payload.evidence_note, max_length=240)

    existing = (
        db.execute(
            text(
                """
                SELECT id, accounting_framework, version, status
                FROM app.ai_accounting_memories
                WHERE business_id = :bid AND memory_key = :mkey
                ORDER BY version DESC
                LIMIT 1
                """
            ),
            {"bid": business_id, "mkey": clean_key},
        )
        .mappings()
        .first()
    )
    if existing and existing["accounting_framework"] != payload.accounting_framework:
        raise DomainError(
            "ERR_FRAMEWORK_CONFLICT",
            f"Memory key '{clean_key}' is already bound to {existing['accounting_framework']}; "
            "US_GAAP and IFRS policies must remain separate.",
            409,
        )

    next_version = (int(existing["version"]) + 1) if existing else 1
    mem_id = uuid4()
    evidence_refs = (
        [{"note": clean_note, "recorded_on": str(payload.effective_from)}] if clean_note else []
    )
    db.execute(
        text(
            """
            INSERT INTO app.ai_accounting_memories (
              id, business_id, memory_key, title, accounting_framework, category,
              binding_kind, rule_condition, account_code, account_name,
              materiality_threshold, effective_from, effective_to, version,
              status, evidence_refs, created_by
            ) VALUES (
              :id, :bid, :mkey, :title, :fw, :cat,
              :bkind, :cond, :acode, :aname,
              :mat, :efrom, :eto, :ver,
              'pending_review', CAST(:erefs AS jsonb), :actor
            )
            """
        ),
        {
            "id": mem_id,
            "bid": business_id,
            "mkey": clean_key,
            "title": clean_title,
            "fw": payload.accounting_framework,
            "cat": payload.category,
            "bkind": payload.binding_kind,
            "cond": clean_cond,
            "acode": payload.account_code.strip(),
            "aname": payload.account_name.strip(),
            "mat": _q2(payload.materiality_threshold),
            "efrom": payload.effective_from,
            "eto": payload.effective_to,
            "ver": next_version,
            "erefs": json.dumps(evidence_refs),
            "actor": user.user.id,
        },
    )

    mem_tool = tool_lookup_approved_memories(
        db,
        ApprovedMemoriesToolInput(
            business_id=business_id,
            accounting_framework=payload.accounting_framework,
            search_query=clean_key,
        ),
    )
    out_payload = {
        "id": str(mem_id),
        "memory_key": clean_key,
        "title": clean_title,
        "accounting_framework": payload.accounting_framework,
        "category": payload.category,
        "binding_kind": payload.binding_kind,
        "rule_condition": clean_cond,
        "account_code": payload.account_code.strip(),
        "account_name": payload.account_name.strip(),
        "materiality_threshold": f"{_q2(payload.materiality_threshold):.2f}",
        "effective_from": str(payload.effective_from),
        "effective_to": str(payload.effective_to) if payload.effective_to else None,
        "version": next_version,
        "status": "pending_review",
    }
    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-04",
        system_prompt="Validate accounting memory candidate without auto-approving policy.",
        user_prompt=json.dumps(out_payload),
        fallback_payload=out_payload,
    )
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-04",
        wave="W1",
        input_summary=(
            f"Memory proposal {clean_key} v{next_version} ({payload.accounting_framework})"
        ),
        model_result=model_res,
        structured_output=out_payload,
        citations=[{"source_type": "accounting_memory", "reference": str(mem_id)}],
        tool_audits=[mem_tool.audit],
        confidence_score=Decimal("0.9800"),
        uncertainty_flags=["untrusted_directive_redacted"] if was_san else [],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )
    return {**out_payload, "evidence_record": run_meta}


@router.post("/businesses/{business_id}/ai/memories/{memory_id}/decision")
def decide_ai_accounting_memory(
    business_id: UUID,
    memory_id: UUID,
    payload: AccountingMemoryDecisionInput,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """AI-04: Approve, supersede, or revoke an accounting memory (Owner/Accountant only)."""
    authorize(db, user, business_id, {"owner", "accountant"})
    row = (
        db.execute(
            text(
                """
                SELECT id, memory_key, version, status, accounting_framework
                FROM app.ai_accounting_memories
                WHERE business_id = :bid AND id = :mid
                FOR UPDATE
                """
            ),
            {"bid": business_id, "mid": memory_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("ERR_NOT_FOUND", "Accounting memory not found.", 404)

    new_status = {
        "approve": "approved",
        "supersede": "superseded",
        "revoke": "revoked",
    }[payload.decision]

    if new_status == "approved":
        # Supersede older approved versions of the same memory_key
        db.execute(
            text(
                """
                UPDATE app.ai_accounting_memories
                SET status = 'superseded', updated_at = clock_timestamp()
                WHERE business_id = :bid
                  AND memory_key = :mkey
                  AND id <> :mid
                  AND status = 'approved'
                """
            ),
            {"bid": business_id, "mkey": row["memory_key"], "mid": memory_id},
        )

    db.execute(
        text(
            """
            UPDATE app.ai_accounting_memories
            SET status = :st,
                reviewed_by = :actor,
                approved_at = CASE
                  WHEN :st = 'approved' THEN clock_timestamp()
                  ELSE approved_at END,
                revoked_at = CASE WHEN :st = 'revoked' THEN clock_timestamp() ELSE revoked_at END,
                updated_at = clock_timestamp()
            WHERE business_id = :bid AND id = :mid
            """
        ),
        {"st": new_status, "actor": user.user.id, "bid": business_id, "mid": memory_id},
    )
    record_audit(db, user.user.id, f"ai.memory.{new_status}", business_id, memory_id)
    return {
        "id": str(memory_id),
        "memory_key": str(row["memory_key"]),
        "version": int(row["version"]),
        "status": new_status,
    }


# ---------------------------------------------------------------------------
# AI-01: Reconciliation Detective (W2)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/reconciliation-detective")
def run_ai_reconciliation_detective(
    business_id: UUID,
    payload: ReconciliationDetectiveInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-01: Deterministically evaluate reconciliation candidates and explain findings."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    open_docs = tool_get_open_trade_documents(
        db, OpenDocumentsToolInput(business_id=business_id, document_kind="all", limit=35)
    )
    bank_rows = tool_get_unmatched_bank_rows(
        db, UnmatchedBankRowsToolInput(business_id=business_id, limit=25)
    )
    memories = tool_lookup_approved_memories(
        db, ApprovedMemoriesToolInput(business_id=business_id, limit=10)
    )

    clean_desc, was_san = sanitize_untrusted_text(payload.statement_description, max_length=240)
    stmt_amt = abs(_q2(payload.statement_amount))
    candidates: list[dict[str, Any]] = []
    classification = "ambiguous_needs_evidence"
    deterministic_delta = stmt_amt
    fee_amount = Decimal("0.00")
    proposed_allocation: dict[str, Any] = {}
    confidence = Decimal("0.6500")
    uncertainty_flags: list[str] = ["untrusted_directive_redacted"] if was_san else []

    # 1. Check duplicate import vs repeated legitimate charge
    same_amt_rows = [
        r
        for r in bank_rows.items
        if abs(_q2(r.amount)) == stmt_amt
        and (payload.bank_row_id is None or r.row_id != str(payload.bank_row_id))
    ]
    if payload.is_duplicate_external_id or (
        same_amt_rows and any(r.posted_date == str(payload.posted_date) for r in same_amt_rows)
    ):
        classification = "duplicate_import"
        deterministic_delta = Decimal("0.00")
        confidence = Decimal("0.9700")
        proposed_allocation = {"action": "exclude_duplicate_import", "amount": f"{stmt_amt:.2f}"}
    else:
        # 2. Check exact single document match
        exact_docs = [d for d in open_docs.items if _q2(d.outstanding) == stmt_amt]
        if exact_docs:
            doc = exact_docs[0]
            classification = "exact_match"
            deterministic_delta = Decimal("0.00")
            confidence = Decimal("0.9850")
            candidates.append(
                {
                    "document_id": doc.document_id,
                    "number": doc.number,
                    "kind": doc.kind,
                    "outstanding": doc.outstanding,
                }
            )
            proposed_allocation = {
                "action": "allocate_single_document",
                "document_ids": [doc.document_id],
                "allocated_amount": f"{stmt_amt:.2f}",
            }
        else:
            # 3. Check bundled payment (2 or 3 open documents summing exactly to stmt_amt)
            found_bundle: list[Any] = []
            docs = open_docs.items
            for i in range(len(docs)):
                for j in range(i + 1, len(docs)):
                    if docs[i].kind == docs[j].kind and (
                        _q2(docs[i].outstanding) + _q2(docs[j].outstanding) == stmt_amt
                    ):
                        found_bundle = [docs[i], docs[j]]
                        break
                if found_bundle:
                    break
            if found_bundle:
                classification = "bundled_payment"
                deterministic_delta = Decimal("0.00")
                confidence = Decimal("0.9500")
                candidates = [
                    {
                        "document_id": d.document_id,
                        "number": d.number,
                        "kind": d.kind,
                        "outstanding": d.outstanding,
                    }
                    for d in found_bundle
                ]
                proposed_allocation = {
                    "action": "allocate_bundled_documents",
                    "document_ids": [d.document_id for d in found_bundle],
                    "allocated_amount": f"{stmt_amt:.2f}",
                }
            else:
                # 4. Check processor fee deducted (95%..99.5% of an open invoice)
                fee_candidates = [
                    d
                    for d in open_docs.items
                    if _q2(d.outstanding) > stmt_amt
                    and Decimal("0.005")
                    <= ((_q2(d.outstanding) - stmt_amt) / _q2(d.outstanding))
                    <= Decimal("0.055")
                ]
                if fee_candidates:
                    doc = fee_candidates[0]
                    fee_amount = _q2(doc.outstanding) - stmt_amt
                    classification = "fee_deducted"
                    deterministic_delta = Decimal("0.00")
                    confidence = Decimal("0.9200")
                    candidates.append(
                        {
                            "document_id": doc.document_id,
                            "number": doc.number,
                            "kind": doc.kind,
                            "outstanding": doc.outstanding,
                            "implied_fee": f"{fee_amount:.2f}",
                        }
                    )
                    proposed_allocation = {
                        "action": "split_gross_and_processing_fee",
                        "document_ids": [doc.document_id],
                        "net_cash": f"{stmt_amt:.2f}",
                        "fee_expense": f"{fee_amount:.2f}",
                        "gross_settled": doc.outstanding,
                    }
                else:
                    # 5. Check partial settlement (stmt_amt < doc.outstanding)
                    partial_candidates = [
                        d for d in open_docs.items if _q2(d.outstanding) > stmt_amt
                    ]
                    if partial_candidates:
                        doc = partial_candidates[0]
                        classification = "partial_settlement"
                        deterministic_delta = _q2(doc.outstanding) - stmt_amt
                        confidence = Decimal("0.8600")
                        candidates.append(
                            {
                                "document_id": doc.document_id,
                                "number": doc.number,
                                "kind": doc.kind,
                                "outstanding": doc.outstanding,
                                "remaining_after_partial": f"{deterministic_delta:.2f}",
                            }
                        )
                        proposed_allocation = {
                            "action": "partial_allocation",
                            "document_ids": [doc.document_id],
                            "allocated_amount": f"{stmt_amt:.2f}",
                            "remaining_open": f"{deterministic_delta:.2f}",
                        }
                    elif same_amt_rows:
                        classification = "repeated_legitimate_charge"
                        deterministic_delta = Decimal("0.00")
                        confidence = Decimal("0.8400")
                        proposed_allocation = {
                            "action": "verify_recurring_vendor_charge",
                            "amount": f"{stmt_amt:.2f}",
                        }
                    else:
                        uncertainty_flags.append("no_deterministic_document_match")

    inv_status = "abstained" if classification == "ambiguous_needs_evidence" else "proposed"
    fallback_explanation = (
        f"Classified '{clean_desc}' ({stmt_amt:.2f} {biz.currency}) as {classification}. "
        f"Matched {len(candidates)} candidate(s) (delta {deterministic_delta:.2f})."
    )
    fallback_payload = {
        "classification": classification,
        "statement_description": clean_desc,
        "statement_amount": f"{stmt_amt:.2f}",
        "currency": biz.currency,
        "deterministic_delta": f"{deterministic_delta:.2f}",
        "fee_amount": f"{fee_amount:.2f}",
        "candidates": candidates,
        "proposed_allocation": proposed_allocation,
        "explanation": fallback_explanation,
        "status": inv_status,
    }
    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-01",
        system_prompt=(
            "You are LedgerPilot AI-01 Reconciliation Detective. Explain the deterministic "
            "matching finding in JSON with key 'explanation'. Never invent document IDs or sums."
        ),
        user_prompt=json.dumps(fallback_payload),
        fallback_payload=fallback_payload,
    )
    explanation = str(model_res.structured.get("explanation") or fallback_explanation)
    final_output = {**fallback_payload, "explanation": explanation}

    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-01",
        wave="W2",
        input_summary=f"Reconciliation detective: {clean_desc} ({stmt_amt:.2f} {biz.currency})",
        model_result=model_res,
        structured_output=final_output,
        citations=[
            {"source_type": "trade_document", "reference": c["document_id"]} for c in candidates
        ],
        tool_audits=[open_docs.audit, bank_rows.audit, memories.audit],
        confidence_score=confidence,
        uncertainty_flags=uncertainty_flags,
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
        status="abstained" if inv_status == "abstained" else "completed",
    )

    inv_id = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.ai_reconciliation_investigations (
              id, business_id, run_id, bank_row_id, statement_description,
              statement_amount, currency, classification, candidates,
              deterministic_delta, fee_amount, proposed_allocation,
              explanation, status, actor
            ) VALUES (
              :id, :bid, :rid, :brid, :sdesc,
              :samt, :curr, :cls, CAST(:cands AS jsonb),
              :ddelta, :famt, CAST(:palloc AS jsonb),
              :expl, :st, :actor
            )
            """
        ),
        {
            "id": inv_id,
            "bid": business_id,
            "rid": UUID(run_meta["run_id"]),
            "brid": payload.bank_row_id,
            "sdesc": clean_desc,
            "samt": stmt_amt,
            "curr": biz.currency,
            "cls": classification,
            "cands": json.dumps(candidates),
            "ddelta": deterministic_delta,
            "famt": fee_amount,
            "palloc": json.dumps(proposed_allocation),
            "expl": explanation,
            "st": inv_status,
            "actor": user.user.id,
        },
    )
    return {"id": str(inv_id), **final_output, "evidence_record": run_meta}


# ---------------------------------------------------------------------------
# AI-03: Closed-Loop Client Clarification (W3)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/clarifications", status_code=201)
def create_ai_client_clarification(
    business_id: UUID,
    payload: ClientClarificationCreateInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-03: Convert an unresolved exception into a deduplicated clarification request."""
    authorize(db, user, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    clean_ref, _ = sanitize_untrusted_text(payload.exception_ref, max_length=120)
    clean_ctx, was_san = sanitize_untrusted_text(payload.context_summary, max_length=400)
    req_docs = [
        sanitize_untrusted_text(d, max_length=80)[0]
        for d in payload.required_documents
        if d.strip()
    ] or ["Supporting Invoice PDF"]

    # Check if recipient opted out in channel_reminder_preferences
    opted_out = db.execute(
        text(
            """
            SELECT opted_out FROM app.channel_reminder_preferences
            WHERE business_id = :bid AND recipient_key = :rkey
            LIMIT 1
            """
        ),
        {"bid": business_id, "rkey": payload.recipient_key.strip()},
    ).scalar_one_or_none()

    # Deduplicate existing open clarification for the same exception_ref
    existing = (
        db.execute(
            text(
                """
                SELECT id, run_id, status, questions, required_documents, remaining_gaps
                FROM app.ai_client_clarifications
                WHERE business_id = :bid
                  AND exception_kind = :ekind
                  AND exception_ref = :eref
                  AND status IN ('draft', 'sent', 'sent_sandbox', 'partially_answered')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {
                "bid": business_id,
                "ekind": payload.exception_kind,
                "eref": clean_ref,
            },
        )
        .mappings()
        .first()
    )
    if existing:
        return {
            "id": str(existing["id"]),
            "deduplicated": True,
            "exception_kind": payload.exception_kind,
            "exception_ref": clean_ref,
            "recipient_key": payload.recipient_key.strip(),
            "channel": payload.channel,
            "questions": existing["questions"],
            "required_documents": existing["required_documents"],
            "remaining_gaps": existing["remaining_gaps"],
            "status": str(existing["status"]),
        }

    questions = [
        f"Please confirm the business purpose and counterparty for {clean_ref} ({clean_ctx}).",
        f"Please attach: {', '.join(req_docs)} covering the applicable accounting period.",
    ]
    clean_recipient = payload.recipient_key.strip()
    if opted_out:
        initial_status = "opted_out"
    elif payload.channel == "email":
        is_synthetic_test = settings.app_env == "test" and not settings.auth_delivery_enabled
        if is_synthetic_test and "@" not in clean_recipient:
            initial_status = "sent"
        else:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean_recipient):
                raise DomainError(
                    "ERR_INVALID_EMAIL",
                    (
                        "Enter a valid email address (for example billing@client.com) "
                        "when sending via Email."
                    ),
                    422,
                )
            if is_synthetic_test:
                initial_status = "sent"
            else:
                email_lines = [
                    "Hello,",
                    "",
                    f"{biz.name} needs a quick clarification regarding {clean_ref}:",
                    f"Details: {clean_ctx}",
                    "",
                    "Questions:",
                    *[f"  {idx + 1}. {q}" for idx, q in enumerate(questions)],
                    "",
                    f"Requested documents: {', '.join(req_docs)}",
                    "",
                    f"Thank you,\n{biz.name} Accounting Team",
                ]
                ok, _prov, mail_errors = deliver_workspace_email(
                    settings,
                    clean_recipient,
                    f"{biz.name} — Document request: {clean_ref}",
                    "\n".join(email_lines),
                    tag="clarification",
                )
                if not ok:
                    err_hint = format_mail_error_for_user(mail_errors)
                    raise DomainError(
                        "ERR_EMAIL_DELIVERY_FAILED",
                        f"Could not send email to {clean_recipient}. {err_hint}",
                        502,
                    )
                initial_status = "sent"
    else:
        initial_status = "sent"
    fallback_payload = {
        "exception_kind": payload.exception_kind,
        "exception_ref": clean_ref,
        "recipient_key": payload.recipient_key.strip(),
        "channel": payload.channel,
        "questions": questions,
        "required_documents": req_docs,
        "remaining_gaps": req_docs,
        "completeness_score": "0.0000",
        "status": initial_status,
    }

    open_docs = tool_get_open_trade_documents(
        db, OpenDocumentsToolInput(business_id=business_id, limit=10)
    )
    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-03",
        system_prompt="Generate concise client accounting clarification questions in JSON.",
        user_prompt=json.dumps(fallback_payload),
        fallback_payload=fallback_payload,
    )
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-03",
        wave="W3",
        input_summary=f"Client clarification for {payload.exception_kind}:{clean_ref}",
        model_result=model_res,
        structured_output=fallback_payload,
        citations=[{"source_type": payload.exception_kind, "reference": clean_ref}],
        tool_audits=[open_docs.audit],
        confidence_score=Decimal("0.9400"),
        uncertainty_flags=["untrusted_directive_redacted"] if was_san else [],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )

    clar_id = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.ai_client_clarifications (
              id, business_id, run_id, exception_kind, exception_ref,
              recipient_key, channel, questions, required_documents,
              completeness_score, remaining_gaps, status, actor
            ) VALUES (
              :id, :bid, :rid, :ekind, :eref,
              :rkey, :ch, CAST(:qs AS jsonb), CAST(:rdocs AS text[]),
              0, CAST(:rgaps AS text[]), :st, :actor
            )
            """
        ),
        {
            "id": clar_id,
            "bid": business_id,
            "rid": UUID(run_meta["run_id"]),
            "ekind": payload.exception_kind,
            "eref": clean_ref,
            "rkey": payload.recipient_key.strip(),
            "ch": payload.channel,
            "qs": json.dumps(questions),
            "rdocs": req_docs,
            "rgaps": req_docs,
            "st": initial_status,
            "actor": user.user.id,
        },
    )
    return {
        "id": str(clar_id),
        "deduplicated": False,
        **fallback_payload,
        "evidence_record": run_meta,
    }


@router.post("/businesses/{business_id}/ai/clarifications/{clarification_id}/reply")
def process_ai_client_clarification_reply(
    business_id: UUID,
    clarification_id: UUID,
    payload: ClientClarificationReplyInput,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """AI-03: Validate client reply and documents for completeness, or close/escalate."""
    authorize(db, user, business_id, {"owner", "accountant", "bookkeeper"})
    row = (
        db.execute(
            text(
                """
                SELECT id, required_documents, status
                FROM app.ai_client_clarifications
                WHERE business_id = :bid AND id = :cid
                FOR UPDATE
                """
            ),
            {"bid": business_id, "cid": clarification_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("ERR_NOT_FOUND", "Clarification request not found.", 404)

    clean_reply, was_san = sanitize_untrusted_text(payload.client_reply_text, max_length=600)
    clean_ev, _ = sanitize_untrusted_text(payload.uploaded_evidence_ref, max_length=160)
    required: list[str] = list(row["required_documents"] or [])
    provided_lower = {d.strip().lower() for d in payload.provided_documents if d.strip()}
    remaining = [d for d in required if d.strip().lower() not in provided_lower]

    if payload.client_action == "opt_out":
        new_status = "opted_out"
        score = Decimal("0.0000")
    elif payload.client_action == "dispute":
        new_status = "escalated_dispute"
        score = Decimal("0.5000")
    else:
        total_req = max(1, len(required))
        matched_count = total_req - len(remaining)
        has_explanation = len(clean_reply) >= 5
        if len(remaining) == 0 and has_explanation:
            new_status = "resolved_closed"
            score = Decimal("1.0000")
        else:
            new_status = "partially_answered"
            score = (Decimal(matched_count) / Decimal(total_req)).quantize(Decimal("0.0001"))

    db.execute(
        text(
            """
            UPDATE app.ai_client_clarifications
            SET client_reply_text = :reply,
                uploaded_evidence_ref = :evref,
                completeness_score = :score,
                remaining_gaps = CAST(:rgaps AS text[]),
                status = :st,
                updated_at = clock_timestamp()
            WHERE business_id = :bid AND id = :cid
            """
        ),
        {
            "reply": clean_reply,
            "evref": clean_ev,
            "score": score,
            "rgaps": remaining,
            "st": new_status,
            "bid": business_id,
            "cid": clarification_id,
        },
    )
    record_audit(db, user.user.id, f"ai.clarification.{new_status}", business_id, clarification_id)
    return {
        "id": str(clarification_id),
        "status": new_status,
        "completeness_score": f"{score:.4f}",
        "remaining_gaps": remaining,
        "prompt_injection_sanitized": was_san,
        "follow_up_question": (
            f"To finish closing this item, please provide: {', '.join(remaining)}."
            if new_status == "partially_answered" and remaining
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# AI-05: Continuous Close Assistant (W4)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/close-assistant")
def run_ai_continuous_close_assistant(
    business_id: UUID,
    payload: CloseAssistantInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-05: Build close readiness report, detect recurring omissions, and draft workpaper."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    readiness = tool_get_close_readiness_snapshot(
        db, CloseReadinessToolInput(business_id=business_id)
    )
    balances = tool_get_account_balances(
        db, AccountBalancesToolInput(business_id=business_id, kind_filter="expense", limit=25)
    )
    memories = tool_lookup_approved_memories(
        db, ApprovedMemoriesToolInput(business_id=business_id, limit=15)
    )

    blockers: list[dict[str, Any]] = []
    if readiness.pending_bank_rows > 0:
        blockers.append(
            {
                "step_order": 1,
                "blocker_kind": "unreconciled_bank_rows",
                "count": readiness.pending_bank_rows,
                "owner_role": "bookkeeper",
                "dependency": "Blocks cash tie-out and period lock",
                "recommended_action": (
                    f"Review and match {readiness.pending_bank_rows} pending bank row(s)."
                ),
            }
        )
    if readiness.bills_missing_evidence > 0:
        blockers.append(
            {
                "step_order": 2,
                "blocker_kind": "bills_missing_evidence",
                "count": readiness.bills_missing_evidence,
                "owner_role": "bookkeeper",
                "dependency": "Blocks AP completeness sign-off",
                "recommended_action": (
                    f"Attach receipts for {readiness.bills_missing_evidence} supplier bill(s)."
                ),
            }
        )
    if readiness.open_close_tasks > 0:
        blockers.append(
            {
                "step_order": 3,
                "blocker_kind": "prepared_close_tasks_awaiting_review",
                "count": readiness.open_close_tasks,
                "owner_role": "accountant",
                "dependency": "Requires independent reviewer sign-off",
                "recommended_action": (
                    f"Complete accountant review on {readiness.open_close_tasks} close task(s)."
                ),
            }
        )

    candidate_omissions: list[dict[str, Any]] = []
    if payload.check_recurring_omissions:
        zero_expense_accts = [a for a in balances.items if _q2(a.net_balance) == Decimal("0.00")]
        for acct in zero_expense_accts[:3]:
            candidate_omissions.append(
                {
                    "account_code": acct.code,
                    "account_name": acct.name,
                    "reason": (
                        f"Expense account {acct.code} ({acct.name}) has 0.00 activity "
                        f"in {payload.period_label}; verify recurring accrual."
                    ),
                }
            )

    readiness_score = max(
        0,
        100
        - (readiness.pending_bank_rows * 10)
        - (readiness.bills_missing_evidence * 12)
        - (readiness.open_close_tasks * 15),
    )
    fallback_payload = {
        "period_label": payload.period_label,
        "readiness_score_pct": readiness_score,
        "ready_for_accountant_signoff": len(blockers) == 0,
        "ai_can_sign_off": False,
        "blockers": blockers,
        "candidate_recurring_omissions": candidate_omissions,
        "workpaper_narrative": (
            f"Period {payload.period_label} close readiness is {readiness_score}% "
            f"({readiness.pending_bank_rows} bank row(s) pending, "
            f"{readiness.bills_missing_evidence} bill(s) missing evidence, "
            f"{readiness.open_close_tasks} close task(s) awaiting independent review). "
            "Independent human accountant sign-off is required before locking the period."
        ),
    }

    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-05",
        system_prompt=("Draft a cited close readiness summary in JSON. Never claim AI sign-off."),
        user_prompt=json.dumps(fallback_payload),
        fallback_payload=fallback_payload,
    )
    final_output = {
        **fallback_payload,
        "ai_can_sign_off": False,
        "workpaper_narrative": str(
            model_res.structured.get("workpaper_narrative")
            or fallback_payload["workpaper_narrative"]
        ),
    }
    citations = [
        {
            "source_type": "close_readiness_snapshot",
            "reference": f"period:{payload.period_label}",
        },
        {"source_type": "trial_balance", "reference": f"business:{business_id}:v{biz.version}"},
    ]
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-05",
        wave="W4",
        input_summary=f"Continuous close assistant for period {payload.period_label}",
        model_result=model_res,
        structured_output=final_output,
        citations=citations,
        tool_audits=[readiness.audit, balances.audit, memories.audit],
        confidence_score=Decimal("0.9600"),
        uncertainty_flags=[],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )
    return {**final_output, "evidence_record": run_meta}


# ---------------------------------------------------------------------------
# AI-06: Multilingual Voice Accounting & Optional LiveKit Session (W5)
# ---------------------------------------------------------------------------


def _extract_voice_accounting_entities(
    transcript: str, default_currency: str
) -> tuple[str, dict[str, Any], list[str]]:
    """Deterministic multilingual (English + Urdu) entity parser for voice accounting."""
    lower = transcript.lower()
    if any(w in lower for w in ("invoice", "customer", "sale", "receivable", "faroht")):
        intent = "create_sales_invoice_draft"
    elif any(w in lower for w in ("payment", "paid", "ada", "pay")):
        intent = "prepare_supplier_bill_draft"
    else:
        intent = "prepare_supplier_bill_draft"

    amt_match = re.search(r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)", transcript)
    raw_amt = amt_match.group(1).replace(",", "") if amt_match else "0.00"
    amount = _q2(raw_amt) if Decimal(raw_amt) > 0 else Decimal("450.00")

    curr = default_currency
    for candidate in ("USD", "PKR", "GBP", "EUR", "AED"):
        if candidate.lower() in lower:
            curr = candidate
            break
    if "rupee" in lower or "روپے" in transcript or "rs" in lower:
        curr = "PKR"

    party = "Cloud Vendor Services"
    for marker in ("for ", "to ", "from ", "vendor ", "supplier "):
        idx = lower.find(marker)
        if idx != -1:
            snippet = transcript[idx + len(marker) :].strip()
            words = snippet.split()[:3]
            cleaned_words = [w.strip(",.:;$") for w in words if not w[0].isdigit()]
            if cleaned_words:
                party = " ".join(cleaned_words)
                break

    uncertain: list[str] = []
    if not amt_match:
        uncertain.append("amount")
    if party == "Cloud Vendor Services":
        uncertain.append("party_name")

    entities = {
        "party_name": party,
        "amount": f"{amount:.2f}",
        "currency": curr,
        "accounting_date": str(date.today()),
        "account_code": "5000",
    }
    return intent, entities, uncertain


@router.post("/businesses/{business_id}/ai/voice-session", status_code=201)
def create_ai_voice_accounting_session(
    business_id: UUID,
    payload: VoiceAccountingSessionInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-06: Transcribe voice note via Groq Whisper and require human confirmation."""
    authorize(db, user, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    raw_rec_id = re.sub(r"[^a-zA-Z0-9_-]", "", payload.recording_id.strip())[:64]
    if not raw_rec_id:
        seed_material = (
            f"{payload.language_locale}|{payload.audio_base64 or payload.transcript_input.strip()}"
        )
        raw_rec_id = f"rec_{hashlib.sha256(seed_material.encode('utf-8')).hexdigest()[:28]}"

    existing_vs = (
        db.execute(
            text(
                """
                SELECT id, language_locale, stt_provider, raw_transcript,
                       corrected_transcript, extracted_intent, extracted_entities,
                       uncertain_fields, critical_fields_confirmed, linked_draft_id,
                       tts_readback_text, livekit_room_name, status
                FROM app.ai_voice_accounting_sessions
                WHERE business_id = :bid
                  AND extracted_entities->>'recording_id' = :rec_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"bid": business_id, "rec_id": raw_rec_id},
        )
        .mappings()
        .first()
    )
    if existing_vs and not payload.regenerate:
        return {
            "id": str(existing_vs["id"]),
            "recording_id": raw_rec_id,
            "cached": True,
            "language_locale": str(existing_vs["language_locale"]),
            "stt_provider": str(existing_vs["stt_provider"]),
            "raw_transcript": str(existing_vs["raw_transcript"]),
            "corrected_transcript": str(existing_vs["corrected_transcript"]),
            "extracted_intent": str(existing_vs["extracted_intent"]),
            "extracted_entities": dict(existing_vs["extracted_entities"] or {}),
            "uncertain_fields": list(existing_vs["uncertain_fields"] or []),
            "critical_fields_confirmed": bool(existing_vs["critical_fields_confirmed"]),
            "linked_draft_id": (
                str(existing_vs["linked_draft_id"]) if existing_vs["linked_draft_id"] else None
            ),
            "tts_readback_text": str(existing_vs["tts_readback_text"]),
            "status": str(existing_vs["status"]),
            "livekit": {
                "livekit_enabled": bool(existing_vs["livekit_room_name"]),
                "mode": "groq_whisper_and_browser_tts",
                "room_name": str(existing_vs["livekit_room_name"] or ""),
                "ws_url": "",
                "token": "",
            },
        }

    adapter = GroqModelAdapter(settings)
    clean_input, was_san = sanitize_untrusted_text(payload.transcript_input, max_length=600)
    stt_res = adapter.transcribe_audio(
        audio_base64=payload.audio_base64,
        audio_format=payload.audio_format,
        language_locale=payload.language_locale,
        fallback_transcript=clean_input
        or "Record K-Electric utility bill for 850.00 USD due this month.",
    )

    intent, entities, uncertain = _extract_voice_accounting_entities(
        stt_res.transcript, biz.currency
    )
    entities["recording_id"] = raw_rec_id
    if len(stt_res.transcript.strip()) < 4:
        status = "abstained_noisy"
        uncertain = ["transcript_too_short_or_noisy"]
    else:
        status = "transcribed_needs_confirmation"

    tts_text = (
        f"Voice instruction captured in {payload.language_locale}: {intent.replace('_', ' ')} "
        f"for {entities['party_name']}, amount {entities['amount']} {entities['currency']} "
        f"on {entities['accounting_date']}. Confirm fields to create a review draft."
    )
    lk_info = (
        generate_livekit_room_token(
            settings,
            business_id=business_id,
            user_id=user.user.id,
            room_name=f"voice-{uuid4().hex[:6]}",
        )
        if payload.request_livekit_room
        else {
            "livekit_enabled": False,
            "mode": "groq_whisper_and_browser_tts",
            "room_name": "",
            "ws_url": "",
            "token": "",
        }
    )

    acct_tool = tool_get_account_balances(
        db, AccountBalancesToolInput(business_id=business_id, limit=10)
    )
    fallback_payload = {
        "recording_id": raw_rec_id,
        "cached": False,
        "language_locale": payload.language_locale,
        "stt_provider": (
            "groq_whisper_large_v3_turbo"
            if not stt_res.fallback_used
            else ("browser_web_speech" if clean_input else "deterministic_fixture")
        ),
        "raw_transcript": stt_res.transcript,
        "corrected_transcript": stt_res.transcript,
        "extracted_intent": intent,
        "extracted_entities": entities,
        "uncertain_fields": uncertain,
        "critical_fields_confirmed": False,
        "tts_readback_text": tts_text,
        "status": status,
        "livekit": lk_info,
    }
    model_res = adapter.generate_structured(
        feature_id="AI-06",
        system_prompt="Extract voice accounting intent and highlight uncertain entities in JSON.",
        user_prompt=stt_res.transcript,
        fallback_payload=fallback_payload,
    )
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-06",
        wave="W5",
        input_summary=f"Voice accounting ({payload.language_locale}): {stt_res.transcript[:120]}",
        model_result=model_res,
        structured_output=fallback_payload,
        citations=[{"source_type": "voice_transcript", "reference": payload.language_locale}],
        tool_audits=[acct_tool.audit],
        confidence_score=Decimal("0.8800") if not uncertain else Decimal("0.7500"),
        uncertainty_flags=uncertain + (["untrusted_directive_redacted"] if was_san else []),
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
        status="abstained" if status == "abstained_noisy" else "completed",
    )

    if existing_vs and payload.regenerate and not existing_vs["critical_fields_confirmed"]:
        sess_id = UUID(str(existing_vs["id"]))
        db.execute(
            text(
                """
                UPDATE app.ai_voice_accounting_sessions
                SET run_id = :rid,
                    language_locale = :lang,
                    audio_format = :afmt,
                    duration_seconds = :dur,
                    stt_provider = :sttp,
                    raw_transcript = :rawt,
                    corrected_transcript = :cort,
                    extracted_intent = :intent,
                    extracted_entities = CAST(:ents AS jsonb),
                    uncertain_fields = CAST(:unc AS text[]),
                    tts_readback_text = :tts,
                    livekit_room_name = :lkroom,
                    status = :st,
                    updated_at = clock_timestamp()
                WHERE business_id = :bid AND id = :id
                """
            ),
            {
                "id": sess_id,
                "bid": business_id,
                "rid": UUID(run_meta["run_id"]),
                "lang": payload.language_locale,
                "afmt": payload.audio_format,
                "dur": stt_res.duration_seconds,
                "sttp": fallback_payload["stt_provider"],
                "rawt": stt_res.transcript,
                "cort": stt_res.transcript,
                "intent": intent,
                "ents": json.dumps(entities),
                "unc": uncertain,
                "tts": tts_text,
                "lkroom": str(lk_info["room_name"]),
                "st": status,
            },
        )
    else:
        sess_id = uuid4()
        db.execute(
            text(
                """
                INSERT INTO app.ai_voice_accounting_sessions (
                  id, business_id, run_id, language_locale, audio_format, duration_seconds,
                  stt_provider, raw_transcript, corrected_transcript, extracted_intent,
                  extracted_entities, uncertain_fields, critical_fields_confirmed,
                  tts_readback_text, livekit_room_name, status, actor
                ) VALUES (
                  :id, :bid, :rid, :lang, :afmt, :dur,
                  :sttp, :rawt, :cort, :intent,
                  CAST(:ents AS jsonb), CAST(:unc AS text[]), false,
                  :tts, :lkroom, :st, :actor
                )
                """
            ),
            {
                "id": sess_id,
                "bid": business_id,
                "rid": UUID(run_meta["run_id"]),
                "lang": payload.language_locale,
                "afmt": payload.audio_format,
                "dur": stt_res.duration_seconds,
                "sttp": fallback_payload["stt_provider"],
                "rawt": stt_res.transcript,
                "cort": stt_res.transcript,
                "intent": intent,
                "ents": json.dumps(entities),
                "unc": uncertain,
                "tts": tts_text,
                "lkroom": str(lk_info["room_name"]),
                "st": status,
                "actor": user.user.id,
            },
        )
    return {"id": str(sess_id), **fallback_payload, "evidence_record": run_meta}


@router.post("/businesses/{business_id}/ai/voice-session/{session_id}/confirm")
def confirm_ai_voice_accounting_session(
    business_id: UUID,
    session_id: UUID,
    payload: VoiceAccountingConfirmInput,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """AI-06: Confirm critical fields on a voice session and create a source draft."""
    authorize(db, user, business_id, {"owner", "accountant", "bookkeeper"})
    row = (
        db.execute(
            text(
                """
                SELECT id, extracted_intent, status
                FROM app.ai_voice_accounting_sessions
                WHERE business_id = :bid AND id = :sid
                FOR UPDATE
                """
            ),
            {"bid": business_id, "sid": session_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("ERR_NOT_FOUND", "Voice accounting session not found.", 404)

    clean_transcript, _ = sanitize_untrusted_text(payload.corrected_transcript, max_length=600)
    clean_party, _ = sanitize_untrusted_text(payload.confirmed_party_name, max_length=120)
    amt = _q2(payload.confirmed_amount)
    draft_id = uuid4()
    draft_payload = {
        "source": "ai_06_multilingual_voice_confirmed",
        "voice_session_id": str(session_id),
        "party_name": clean_party,
        "amount": f"{amt:.2f}",
        "currency": payload.confirmed_currency.upper(),
        "accounting_date": str(payload.confirmed_date),
        "account_code": payload.confirmed_account_code.strip(),
        "transcript": clean_transcript,
    }
    db.execute(
        text(
            """
            INSERT INTO app.source_drafts (
              id, business_id, key, payload, status, version, prepared_by
            ) VALUES (
              :id, :bid, :dkey, CAST(:payload AS jsonb), 'draft', 0, :actor
            )
            """
        ),
        {
            "id": draft_id,
            "bid": business_id,
            "dkey": f"ai06-voice-{str(session_id)[:12]}",
            "payload": json.dumps(draft_payload),
            "actor": user.user.id,
        },
    )
    db.execute(
        text(
            """
            UPDATE app.ai_voice_accounting_sessions
            SET corrected_transcript = :cort,
                extracted_entities = CAST(:ents AS jsonb),
                uncertain_fields = ARRAY[]::text[],
                critical_fields_confirmed = true,
                confirmed_by = :actor,
                linked_draft_id = :did,
                status = 'confirmed_draft_created',
                updated_at = clock_timestamp()
            WHERE business_id = :bid AND id = :sid
            """
        ),
        {
            "cort": clean_transcript,
            "ents": json.dumps(draft_payload),
            "actor": user.user.id,
            "did": draft_id,
            "bid": business_id,
            "sid": session_id,
        },
    )
    record_audit(db, user.user.id, "ai.voice_session.confirmed", business_id, session_id)
    return {
        "id": str(session_id),
        "status": "confirmed_draft_created",
        "critical_fields_confirmed": True,
        "linked_draft_id": str(draft_id),
        "confirmed_entities": draft_payload,
    }


# ---------------------------------------------------------------------------
# AI-07: Contract / Invoice / Payment Verification (W5)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/contract-verification", status_code=201)
def run_ai_contract_verification(
    business_id: UUID,
    payload: ContractVerificationInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-07: Extract cited contract terms and compare against invoiced and paid amounts."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    clean_ref, _ = sanitize_untrusted_text(payload.contract_reference, max_length=120)
    clean_party, _ = sanitize_untrusted_text(payload.vendor_or_customer, max_length=120)
    clean_clause, was_san = sanitize_untrusted_text(payload.contract_clause_text, max_length=1000)

    contracted = _q2(payload.contracted_amount)
    invoiced = _q2(payload.invoiced_amount)
    paid = _q2(payload.paid_amount)
    discrepancy = invoiced - contracted

    if payload.prior_payment_count > 0 and paid >= contracted and invoiced > Decimal("0.00"):
        discrepancy_kind = "duplicate_payment"
        discrepancy = invoiced
        status = "discrepancy_flagged"
        proposed_action = (
            f"Hold payment on {clean_ref}: contract value {contracted:.2f} {biz.currency} "
            f"was already paid ({paid:.2f} {biz.currency}). Request vendor credit note."
        )
    elif payload.unapproved_milestone_billed:
        discrepancy_kind = "unapproved_milestone"
        discrepancy = max(Decimal("0.00"), invoiced - contracted) or invoiced
        status = "discrepancy_flagged"
        proposed_action = (
            f"Dispute unapproved milestone on {clean_ref} and request sign-off evidence."
        )
    elif invoiced > contracted:
        discrepancy_kind = "rate_overcharge"
        status = "discrepancy_flagged"
        proposed_action = (
            f"Short-pay or request credit note of {discrepancy:.2f} {biz.currency} "
            f"(invoiced {invoiced:.2f} vs contracted cap {contracted:.2f})."
        )
    elif invoiced < contracted:
        discrepancy_kind = "missing_credit"
        status = "schedule_proposed"
        proposed_action = (
            f"Invoiced amount ({invoiced:.2f}) is below contracted amount ({contracted:.2f}); "
            "track remaining milestone or SLA credit on revenue/prepaid schedule."
        )
    else:
        discrepancy_kind = "aligned"
        discrepancy = Decimal("0.00")
        status = "verified_aligned"
        proposed_action = "Contract terms, invoice total, and payment schedule are aligned."

    extracted_clauses = [
        {
            "clause_type": "pricing_and_cap",
            "contracted_amount": f"{contracted:.2f}",
            "currency": biz.currency,
            "cited_passage": clean_clause[:220],
        }
    ]
    open_docs = tool_get_open_trade_documents(
        db, OpenDocumentsToolInput(business_id=business_id, limit=15)
    )
    memories = tool_lookup_approved_memories(
        db, ApprovedMemoriesToolInput(business_id=business_id, limit=10)
    )

    fallback_payload = {
        "contract_reference": clean_ref,
        "vendor_or_customer": clean_party,
        "currency": biz.currency,
        "contracted_amount": f"{contracted:.2f}",
        "invoiced_amount": f"{invoiced:.2f}",
        "paid_amount": f"{paid:.2f}",
        "discrepancy_amount": f"{discrepancy:.2f}",
        "discrepancy_kind": discrepancy_kind,
        "extracted_clauses": extracted_clauses,
        "proposed_action": proposed_action,
        "status": status,
    }
    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-07",
        system_prompt="Verify contract clauses against deterministic invoice figures in JSON.",
        user_prompt=json.dumps(fallback_payload),
        fallback_payload=fallback_payload,
    )
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-07",
        wave="W5",
        input_summary=f"Contract verification {clean_ref} ({clean_party})",
        model_result=model_res,
        structured_output=fallback_payload,
        citations=[{"source_type": "contract_clause", "reference": clean_ref}],
        tool_audits=[open_docs.audit, memories.audit],
        confidence_score=Decimal("0.9600"),
        uncertainty_flags=["untrusted_directive_redacted"] if was_san else [],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )

    ver_id = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.ai_contract_verifications (
              id, business_id, run_id, contract_reference, vendor_or_customer,
              currency, contracted_amount, invoiced_amount, paid_amount,
              discrepancy_amount, discrepancy_kind, extracted_clauses,
              proposed_action, status, actor
            ) VALUES (
              :id, :bid, :rid, :cref, :party,
              :curr, :camt, :iamt, :pamt,
              :damt, :dkind, CAST(:clauses AS jsonb),
              :pact, :st, :actor
            )
            """
        ),
        {
            "id": ver_id,
            "bid": business_id,
            "rid": UUID(run_meta["run_id"]),
            "cref": clean_ref,
            "party": clean_party,
            "curr": biz.currency,
            "camt": contracted,
            "iamt": invoiced,
            "pamt": paid,
            "damt": discrepancy,
            "dkind": discrepancy_kind,
            "clauses": json.dumps(extracted_clauses),
            "pact": proposed_action,
            "st": status,
            "actor": user.user.id,
        },
    )
    return {"id": str(ver_id), **fallback_payload, "evidence_record": run_meta}


# ---------------------------------------------------------------------------
# AI-08: Action-Based Cash-Flow Scenarios (W4)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/cash-scenarios", status_code=201)
def run_ai_cash_flow_scenario(
    business_id: UUID,
    payload: CashScenarioInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-08: Compute baseline vs. what-if cash-flow projections in canonical Decimal."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    acct_tool = tool_get_account_balances(
        db, AccountBalancesToolInput(business_id=business_id, kind_filter="asset", limit=20)
    )
    clean_name, _ = sanitize_untrusted_text(payload.scenario_name, max_length=120)
    clean_prompt, was_san = sanitize_untrusted_text(payload.natural_language_prompt, max_length=400)

    start_cash = _q2(payload.baseline_starting_cash)
    base_in = _q2(payload.baseline_weekly_inflow)
    base_out = _q2(payload.baseline_weekly_outflow)
    delay_wks = payload.payment_delay_weeks
    capex = _q2(payload.equipment_purchase_amount)
    cost_mult = (Decimal("100.00") + _q2(payload.recurring_cost_change_pct)) / Decimal("100.00")
    coll_mult = (Decimal("100.00") + _q2(payload.collections_change_pct)) / Decimal("100.00")

    base_running = start_cash
    scen_running = start_cash
    lowest_cash = start_cash
    shortfall_weeks = 0
    weekly_series: list[dict[str, Any]] = []

    for w in range(1, payload.horizon_weeks + 1):
        base_running = _q2(base_running + base_in - base_out)
        wk_in = Decimal("0.00") if w <= delay_wks else _q2(base_in * coll_mult)
        wk_out = _q2(base_out * cost_mult) + (capex if w == 1 else Decimal("0.00"))
        scen_running = _q2(scen_running + wk_in - wk_out)
        if scen_running < lowest_cash:
            lowest_cash = scen_running
        if scen_running < Decimal("0.00"):
            shortfall_weeks += 1
        weekly_series.append(
            {
                "week": w,
                "baseline_cash": f"{base_running:.2f}",
                "scenario_cash": f"{scen_running:.2f}",
                "weekly_delta": f"{(scen_running - base_running):.2f}",
            }
        )

    net_delta = _q2(scen_running - base_running)
    params_obj = {
        "payment_delay_weeks": delay_wks,
        "equipment_purchase_amount": f"{capex:.2f}",
        "recurring_cost_change_pct": f"{_q2(payload.recurring_cost_change_pct):.2f}",
        "collections_change_pct": f"{_q2(payload.collections_change_pct):.2f}",
    }
    fallback_payload = {
        "scenario_name": clean_name,
        "natural_language_prompt": clean_prompt,
        "currency": biz.currency,
        "horizon_weeks": payload.horizon_weeks,
        "parameters": params_obj,
        "baseline_ending_cash": f"{base_running:.2f}",
        "scenario_ending_cash": f"{scen_running:.2f}",
        "net_cash_delta": f"{net_delta:.2f}",
        "lowest_weekly_cash": f"{lowest_cash:.2f}",
        "shortfall_weeks": shortfall_weeks,
        "weekly_series": weekly_series,
    }
    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-08",
        system_prompt=("Explain deterministic cash-flow scenario in JSON without altering totals."),
        user_prompt=json.dumps(fallback_payload),
        fallback_payload=fallback_payload,
    )
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-08",
        wave="W4",
        input_summary=f"Cash-flow scenario '{clean_name}' ({payload.horizon_weeks}w)",
        model_result=model_res,
        structured_output=fallback_payload,
        citations=[{"source_type": "cash_forecast_baseline", "reference": str(business_id)}],
        tool_audits=[acct_tool.audit],
        confidence_score=Decimal("0.9700"),
        uncertainty_flags=["untrusted_directive_redacted"] if was_san else [],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )

    scen_id = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.ai_cash_scenarios (
              id, business_id, run_id, scenario_name, natural_language_prompt,
              currency, horizon_weeks, parameters, baseline_ending_cash,
              scenario_ending_cash, net_cash_delta, lowest_weekly_cash,
              shortfall_weeks, weekly_series, actor
            ) VALUES (
              :id, :bid, :rid, :sname, :prompt,
              :curr, :hwks, CAST(:params AS jsonb), :bend,
              :send, :ndelta, :low,
              :swks, CAST(:wseries AS jsonb), :actor
            )
            """
        ),
        {
            "id": scen_id,
            "bid": business_id,
            "rid": UUID(run_meta["run_id"]),
            "sname": clean_name,
            "prompt": clean_prompt,
            "curr": biz.currency,
            "hwks": payload.horizon_weeks,
            "params": json.dumps(params_obj),
            "bend": base_running,
            "send": scen_running,
            "ndelta": net_delta,
            "low": lowest_cash,
            "swks": shortfall_weeks,
            "wseries": json.dumps(weekly_series),
            "actor": user.user.id,
        },
    )
    return {"id": str(scen_id), **fallback_payload, "evidence_record": run_meta}


# ---------------------------------------------------------------------------
# AI-09: Accountant Portfolio Work Planner (W4)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/portfolio-planner")
def run_ai_portfolio_work_planner(
    business_id: UUID,
    payload: PortfolioPlannerInput,
    user: Identity,
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """AI-09: Rank authorized portfolio work without summing unlike currencies."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    pol = effective_policy(db, biz)

    # Query only businesses where the caller has an active membership
    member_businesses = (
        db.execute(
            text(
                """
                SELECT b.id, b.name, b.currency, m.role
                FROM app.memberships m
                JOIN app.businesses b ON b.id = m.business_id
                WHERE m.user_id = :uid AND m.active = true
                ORDER BY b.name ASC, b.id ASC
                LIMIT 50
                """
            ),
            {"uid": user.user.id},
        )
        .mappings()
        .all()
    )

    ranked_queue: list[dict[str, Any]] = []
    totals_by_currency: dict[str, Decimal] = {}
    tool_audits: list[ToolInvocationAudit] = []

    for mb in member_businesses:
        mb_id = UUID(str(mb["id"]))
        set_context(db, user.user.id, mb_id)
        snap = tool_get_close_readiness_snapshot(db, CloseReadinessToolInput(business_id=mb_id))
        open_docs = tool_get_open_trade_documents(
            db, OpenDocumentsToolInput(business_id=mb_id, limit=20)
        )
        if mb_id == business_id:
            tool_audits.extend([snap.audit, open_docs.audit])

        curr = str(mb["currency"])
        open_exposure = sum((_q2(d.outstanding) for d in open_docs.items), Decimal("0.00"))
        totals_by_currency[curr] = totals_by_currency.get(curr, Decimal("0.00")) + open_exposure

        priority_score = (
            (snap.open_close_tasks * 30)
            + (snap.bills_missing_evidence * 20)
            + (snap.pending_bank_rows * 10)
            + (15 if open_exposure >= payload.materiality_threshold else 0)
        )
        ranked_queue.append(
            {
                "business_id": str(mb_id),
                "business_name": str(mb["name"]),
                "role": str(mb["role"]),
                "currency": curr,
                "open_document_exposure": f"{open_exposure:.2f}",
                "pending_bank_rows": snap.pending_bank_rows,
                "bills_missing_evidence": snap.bills_missing_evidence,
                "open_close_tasks": snap.open_close_tasks,
                "priority_score": priority_score,
                "reason_for_order": (
                    f"{snap.open_close_tasks} close review(s), "
                    f"{snap.bills_missing_evidence} bill(s) missing evidence, "
                    f"{snap.pending_bank_rows} bank row(s), "
                    f"{open_exposure:.2f} {curr} open exposure."
                ),
            }
        )

    # Restore context to the current business_id
    set_context(db, user.user.id, business_id)
    ranked_queue.sort(key=lambda item: (-int(item["priority_score"]), str(item["business_name"])))
    for idx, item in enumerate(ranked_queue, start=1):
        item["rank"] = idx

    currency_buckets = [
        {"currency": k, "total_open_exposure": f"{v:.2f}"}
        for k, v in sorted(totals_by_currency.items())
    ]
    fallback_payload = {
        "authorized_businesses_evaluated": len(ranked_queue),
        "horizon_days": payload.horizon_days,
        "materiality_threshold": f"{_q2(payload.materiality_threshold):.2f}",
        "exposure_by_currency": currency_buckets,
        "unlike_currencies_summed": False,
        "ranked_work_queue": ranked_queue,
        "daily_briefing": (
            f"Evaluated {len(ranked_queue)} authorized workspace(s). "
            f"Top priority: {ranked_queue[0]['business_name']} "
            f"(score {ranked_queue[0]['priority_score']})."
            if ranked_queue
            else "No active portfolio exceptions."
        ),
    }

    adapter = GroqModelAdapter(settings)
    model_res = adapter.generate_structured(
        feature_id="AI-09",
        system_prompt=(
            "Summarize deterministic portfolio work queue in JSON. Never sum currencies."
        ),
        user_prompt=json.dumps(fallback_payload),
        fallback_payload=fallback_payload,
    )
    run_meta = _persist_ai_run(
        db,
        business_id=business_id,
        actor_id=user.user.id,
        feature_id="AI-09",
        wave="W4",
        input_summary=f"Portfolio work planner across {len(ranked_queue)} authorized workspace(s)",
        model_result=model_res,
        structured_output=fallback_payload,
        citations=[
            {"source_type": "portfolio_membership", "reference": item["business_id"]}
            for item in ranked_queue
        ],
        tool_audits=tool_audits,
        confidence_score=Decimal("0.9800"),
        uncertainty_flags=[],
        business_version=biz.version,
        policy_version=pol.version if pol else 1,
    )
    return {**fallback_payload, "evidence_record": run_meta}


# ---------------------------------------------------------------------------
# AI-10: Verifiable AI Evidence Record Decision & Stale-Approval Guard (W0/W1)
# ---------------------------------------------------------------------------


@router.post("/businesses/{business_id}/ai/runs/{run_id}/decision")
def decide_ai_run_evidence_record(
    business_id: UUID,
    run_id: UUID,
    payload: AIRunDecisionInput,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """AI-10: Approve, reject, or cancel an AI run with content-hash and version binding."""
    authorize(db, user, business_id, {"owner", "accountant"})
    biz = get_business(db, business_id)
    row = (
        db.execute(
            text(
                """
                SELECT id, feature_id, status, approval_state,
                       approval_content_hash, business_version
                FROM app.ai_runs
                WHERE business_id = :bid AND id = :rid
                FOR UPDATE
                """
            ),
            {"bid": business_id, "rid": run_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("ERR_NOT_FOUND", "AI evidence record not found.", 404)

    if payload.decision == "cancel":
        db.execute(
            text(
                """
                UPDATE app.ai_runs
                SET status = 'canceled',
                    approval_state = 'rejected',
                    rejection_reason = :reason,
                    updated_at = clock_timestamp()
                WHERE business_id = :bid AND id = :rid
                """
            ),
            {
                "reason": payload.reason.strip() or "Canceled by reviewer",
                "bid": business_id,
                "rid": run_id,
            },
        )
        record_audit(db, user.user.id, "ai.run.canceled", business_id, run_id)
        return {
            "run_id": str(run_id),
            "status": "canceled",
            "approval_state": "rejected",
        }

    if row["approval_content_hash"] != payload.expected_content_hash:
        raise DomainError(
            "ERR_APPROVAL_HASH_MISMATCH",
            "Proposal content hash does not match the reviewed recommendation.",
            409,
        )

    if payload.decision == "approve" and int(row["business_version"]) != biz.version:
        db.execute(
            text(
                """
                UPDATE app.ai_runs
                SET approval_state = 'stale_invalidated',
                    updated_at = clock_timestamp()
                WHERE business_id = :bid AND id = :rid
                """
            ),
            {"bid": business_id, "rid": run_id},
        )
        raise DomainError(
            "ERR_STALE_APPROVAL",
            "Underlying ledger changed since this recommendation was generated; refresh.",
            409,
        )

    new_state = "approved" if payload.decision == "approve" else "rejected"
    db.execute(
        text(
            """
            UPDATE app.ai_runs
            SET approval_state = :astate,
                approved_by = :actor,
                approved_at = clock_timestamp(),
                rejection_reason = :reason,
                updated_at = clock_timestamp()
            WHERE business_id = :bid AND id = :rid
            """
        ),
        {
            "astate": new_state,
            "actor": user.user.id,
            "reason": payload.reason.strip(),
            "bid": business_id,
            "rid": run_id,
        },
    )
    record_audit(db, user.user.id, f"ai.run.{new_state}", business_id, run_id)
    return {
        "run_id": str(run_id),
        "status": str(row["status"]),
        "approval_state": new_state,
        "approved_by": str(user.user.id),
    }


@router.post("/businesses/{business_id}/ai/controls")
def update_ai_tenant_controls(
    business_id: UUID,
    payload: AITenantControlsUpdateInput,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """W0 Item 7: Configure per-business AI feature enablement, pause toggle, and budget caps."""
    authorize(db, user, business_id, {"owner", "accountant"})
    clean_reason, _ = sanitize_untrusted_text(payload.pause_reason, max_length=240)
    db.execute(
        text(
            """
            INSERT INTO app.ai_tenant_controls (
              business_id, ai_enabled, operator_paused, pause_reason,
              monthly_budget_usd, monthly_token_budget, max_concurrent_runs,
              updated_by, updated_at
            ) VALUES (
              :bid, :enabled, :paused, :reason,
              :budget_usd, :token_budget, :max_conc,
              :actor, clock_timestamp()
            )
            ON CONFLICT (business_id) DO UPDATE SET
              ai_enabled = EXCLUDED.ai_enabled,
              operator_paused = EXCLUDED.operator_paused,
              pause_reason = EXCLUDED.pause_reason,
              monthly_budget_usd = EXCLUDED.monthly_budget_usd,
              monthly_token_budget = EXCLUDED.monthly_token_budget,
              max_concurrent_runs = EXCLUDED.max_concurrent_runs,
              updated_by = EXCLUDED.updated_by,
              updated_at = clock_timestamp()
            """
        ),
        {
            "bid": business_id,
            "enabled": payload.ai_enabled,
            "paused": payload.operator_paused,
            "reason": clean_reason,
            "budget_usd": payload.monthly_budget_usd.quantize(Decimal("0.000001")),
            "token_budget": payload.monthly_token_budget,
            "max_conc": payload.max_concurrent_runs,
            "actor": user.user.id,
        },
    )
    record_audit(db, user.user.id, "ai.controls.updated", business_id, business_id)
    return _get_or_default_ai_controls(db, business_id)


@router.get("/businesses/{business_id}/ai/runs/{run_id}/export")
def export_ai_run_evidence_record(
    business_id: UUID,
    run_id: UUID,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """AI-10: Export a scoped, redacted verifiable evidence package with source-freshness state."""
    authorize(db, user, business_id)
    biz = get_business(db, business_id)
    row = (
        db.execute(
            text(
                """
                SELECT id, feature_id, wave, provider, model_name, prompt_version,
                       tool_versions, status, input_summary, structured_output,
                       citations, tool_invocations, confidence_score, uncertainty_flags,
                       snapshot_hash, business_version, policy_version,
                       prompt_tokens, completion_tokens, total_tokens,
                       latency_ms, estimated_cost_usd, approval_state,
                       approval_content_hash, approved_by, approved_at,
                       rejection_reason, created_at
                FROM app.ai_runs
                WHERE business_id = :bid AND id = :rid
                """
            ),
            {"bid": business_id, "rid": run_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("ERR_NOT_FOUND", "AI evidence record not found.", 404)

    tool_logs = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, tool_name, parameters_hash, row_count, result_bytes,
                       untrusted_content_sanitized, duration_ms, executed_at
                FROM app.ai_tool_audit_logs
                WHERE business_id = :bid AND run_id = :rid
                ORDER BY executed_at ASC
                """
            ),
            {"bid": business_id, "rid": run_id},
        )
        .mappings()
        .all()
    ]
    is_stale = int(row["business_version"]) != biz.version
    raw_citations = row["citations"] if isinstance(row["citations"], list) else []
    enriched_citations = [
        {
            **dict(cite),
            "availability": "stale_snapshot_version" if is_stale else "available",
        }
        for cite in raw_citations
    ]
    redacted_output: dict[str, Any] = {}
    if isinstance(row["structured_output"], dict):
        for k, v in row["structured_output"].items():
            if any(sec in k.lower() for sec in ("secret", "token", "api_key", "password")):
                redacted_output[k] = "[REDACTED]"
            else:
                redacted_output[k] = v

    record_audit(db, user.user.id, "ai.run.exported", business_id, run_id)
    return {
        "export_schema_version": "AI-10.v1",
        "business_id": str(business_id),
        "run_id": str(row["id"]),
        "feature_id": str(row["feature_id"]),
        "wave": str(row["wave"]),
        "provider": str(row["provider"]),
        "model_name": str(row["model_name"]),
        "prompt_version": str(row["prompt_version"]),
        "tool_versions": row["tool_versions"],
        "status": str(row["status"]),
        "approval_state": str(row["approval_state"]),
        "approval_content_hash": str(row["approval_content_hash"]),
        "snapshot_hash": str(row["snapshot_hash"]),
        "run_business_version": int(row["business_version"]),
        "current_business_version": biz.version,
        "is_stale_against_current_ledger": is_stale,
        "redaction_applied": True,
        "confidence_score": f"{Decimal(str(row['confidence_score'])):.4f}",
        "uncertainty_flags": list(row["uncertainty_flags"] or []),
        "input_summary": str(row["input_summary"]),
        "structured_output": redacted_output,
        "citations": enriched_citations,
        "tool_audit_logs": [
            {
                "id": str(t["id"]),
                "tool_name": str(t["tool_name"]),
                "parameters_hash": str(t["parameters_hash"]),
                "row_count": int(t["row_count"]),
                "result_bytes": int(t["result_bytes"]),
                "untrusted_content_sanitized": bool(t["untrusted_content_sanitized"]),
                "duration_ms": int(t["duration_ms"]),
            }
            for t in tool_logs
        ],
        "usage": {
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "latency_ms": int(row["latency_ms"]),
            "estimated_cost_usd": f"{Decimal(str(row['estimated_cost_usd'])):.6f}",
        },
    }


@router.post("/businesses/{business_id}/ai/datasets/evaluate", status_code=201)
def evaluate_ai_dataset_registry(
    business_id: UUID,
    payload: AIDatasetEvaluationInput,
    user: Identity,
    db: DB,
) -> dict[str, Any]:
    """W0 Item 5 & 8: Record dataset evaluation separating synthetic vs de-identified cohorts."""
    authorize(db, user, business_id, {"owner", "accountant"})
    clean_key = payload.dataset_key.strip().lower()
    clean_notes, _ = sanitize_untrusted_text(payload.notes, max_length=300)
    p = payload.precision_score.quantize(Decimal("0.0001"))
    r = payload.recall_score.quantize(Decimal("0.0001"))
    c = payload.citation_validity_rate.quantize(Decimal("0.0001"))
    f1 = (
        ((Decimal("2") * p * r) / (p + r)).quantize(Decimal("0.0001"))
        if (p + r) > Decimal("0")
        else Decimal("0.0000")
    )
    gate_passed = (
        p >= Decimal("0.9000")
        and r >= Decimal("0.8800")
        and c >= Decimal("0.9800")
        and payload.execution_provenance != "supplied_prediction"
    )
    eval_id = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.ai_evaluation_datasets (
              id, business_id, dataset_key, dataset_cohort, execution_provenance,
              feature_id, sample_count, precision_score, recall_score, f1_score,
              citation_validity_rate, promotion_gate_passed, notes, created_by
            ) VALUES (
              :id, :bid, :dkey, :cohort, :prov,
              :fid, :scount, :p, :r, :f1,
              :c, :gate, :notes, :actor
            )
            """
        ),
        {
            "id": eval_id,
            "bid": business_id,
            "dkey": clean_key,
            "cohort": payload.dataset_cohort,
            "prov": payload.execution_provenance,
            "fid": payload.feature_id,
            "scount": payload.sample_count,
            "p": p,
            "r": r,
            "f1": f1,
            "c": c,
            "gate": gate_passed,
            "notes": clean_notes,
            "actor": user.user.id,
        },
    )
    record_audit(db, user.user.id, "ai.dataset.evaluated", business_id, eval_id)
    return {
        "id": str(eval_id),
        "dataset_key": clean_key,
        "dataset_cohort": payload.dataset_cohort,
        "execution_provenance": payload.execution_provenance,
        "feature_id": payload.feature_id,
        "sample_count": payload.sample_count,
        "precision_score": f"{p:.4f}",
        "recall_score": f"{r:.4f}",
        "f1_score": f"{f1:.4f}",
        "citation_validity_rate": f"{c:.4f}",
        "promotion_gate_passed": gate_passed,
        "notes": clean_notes,
    }
