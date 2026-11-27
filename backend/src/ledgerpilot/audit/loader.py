"""Read-only, bounded, RLS-enforced financial data loader for LangGraph audit runs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account, Journal, JournalLine, Period
from ledgerpilot.ai.adapter import sanitize_untrusted_text
from ledgerpilot.ai.tools import _verify_rls_scope
from ledgerpilot.audit.state import AuditDataset
from ledgerpilot.banking.models import BankCorrection, BankRow, BankRule, Statement
from ledgerpilot.reports import service as report_service
from ledgerpilot.tenancy.models import PolicyHistory
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade.models import Allocation, Party, Payment, TradeDocument
from ledgerpilot.workspace.models import CloseTask, Evidence, EvidenceLink, ReportRun

MAX_AUDIT_RECORDS = 5000


def resolve_earliest_record_date(db: Session, business_id: UUID, default_today: date) -> date:
    """Find the earliest accounting date across journals, bank rows, and trade documents."""
    candidates: list[date] = []
    for stmt in (
        select(func.min(Journal.accounting_date)).where(Journal.business_id == business_id),
        select(func.min(BankRow.accounting_date)).where(BankRow.business_id == business_id),
        select(func.min(TradeDocument.accounting_date)).where(
            TradeDocument.business_id == business_id
        ),
    ):
        found = db.scalar(stmt)
        if isinstance(found, date):
            candidates.append(found)
    return min(candidates) if candidates else default_today.replace(month=1, day=1)


def _safe_optional_rows(db: Session, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Query optional extended tables inside a savepoint so partial schemas never abort."""
    try:
        with db.begin_nested():
            return [dict(r) for r in db.execute(text(sql), params).mappings().all()]
    except Exception:
        return []


def load_audit_dataset(
    db: Session,
    *,
    run_id: str,
    business_id: UUID,
    period_start: date,
    period_end: date,
    scope_filters: dict[str, Any] | None = None,
) -> AuditDataset:
    """Load bounded, read-only financial data for [period_start, period_end] under tenant RLS."""
    _verify_rls_scope(db, business_id)
    business = get_business(db, business_id)
    filters = scope_filters or {}
    max_rows = min(int(filters.get("max_rows", MAX_AUDIT_RECORDS)), MAX_AUDIT_RECORDS)

    accounts_list = list(
        db.scalars(
            select(Account)
            .where(Account.business_id == business_id)
            .order_by(Account.code, Account.id)
            .limit(2000)
        )
    )
    accounts = {
        str(a.id): {
            "id": str(a.id),
            "code": a.code,
            "name": sanitize_untrusted_text(a.name, max_length=120)[0],
            "kind": a.kind,
            "control": a.control,
            "active": bool(a.active),
        }
        for a in accounts_list
    }

    opening_day = period_start - timedelta(days=1)
    snapshots = report_service.balance_snapshots(db, business_id, [opening_day, period_end])
    opening_balances = {str(aid): Decimal(val) for aid, val in snapshots[opening_day].items()}
    closing_balances = {str(aid): Decimal(val) for aid, val in snapshots[period_end].items()}

    period_movements: dict[str, Decimal] = {}
    for aid, net in db.execute(
        select(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0),
        )
        .join(
            Journal,
            (Journal.id == JournalLine.journal_id)
            & (Journal.business_id == JournalLine.business_id),
        )
        .where(
            Journal.business_id == business_id,
            Journal.status == "posted",
            Journal.accounting_date >= period_start,
            Journal.accounting_date <= period_end,
        )
        .group_by(JournalLine.account_id)
    ).all():
        period_movements[str(aid)] = Decimal(net)

    journal_rows = list(
        db.scalars(
            select(Journal)
            .where(
                Journal.business_id == business_id,
                Journal.accounting_date >= period_start,
                Journal.accounting_date <= period_end,
            )
            .order_by(Journal.accounting_date, Journal.id)
            .limit(max_rows)
        )
    )
    journal_ids = [j.id for j in journal_rows]
    journals = [
        {
            "id": str(j.id),
            "accounting_date": j.accounting_date.isoformat(),
            "memo": sanitize_untrusted_text(j.memo, max_length=300)[0],
            "currency": j.currency,
            "status": j.status,
            "source_kind": j.source_kind,
            "source_id": str(j.source_id) if j.source_id else None,
            "reversal_of": str(j.reversal_of) if j.reversal_of else None,
            "period_id": str(j.period_id) if j.period_id else None,
            "created_at": j.created_at.date().isoformat() if j.created_at else None,
            "posted_at": j.posted_at.date().isoformat() if j.posted_at else None,
        }
        for j in journal_rows
    ]

    lines_by_journal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk_start in range(0, len(journal_ids), 500):
        batch_ids = journal_ids[chunk_start : chunk_start + 500]
        for jl in db.scalars(
            select(JournalLine)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.journal_id.in_(batch_ids),
            )
            .order_by(JournalLine.journal_id, JournalLine.position)
        ):
            lines_by_journal[str(jl.journal_id)].append(
                {
                    "id": str(jl.id),
                    "account_id": str(jl.account_id),
                    "position": jl.position,
                    "debit": str(Decimal(jl.debit)),
                    "credit": str(Decimal(jl.credit)),
                }
            )

    all_j_rows = db.execute(
        select(Journal.id, Journal.status)
        .where(Journal.business_id == business_id)
        .limit(max_rows * 2)
    ).all()
    all_journal_ids = frozenset(str(jid) for jid, _ in all_j_rows)
    posted_journal_ids = frozenset(str(jid) for jid, st in all_j_rows if st == "posted")

    computed_bs = report_service.financial(
        db,
        business_id,
        date(1900, 1, 1),
        period_end,
        "accrual",
        "balance-sheet",
    )
    control_tieouts = report_service.controls(db, business_id, period_end)
    tax_tieouts = report_service.tax_support(db, business_id, date(1900, 1, 1), period_end)

    saved_bs_rows = list(
        db.scalars(
            select(ReportRun)
            .where(
                ReportRun.business_id == business_id,
                ReportRun.kind == "balance-sheet",
            )
            .order_by(ReportRun.created_at.desc())
            .limit(50)
        )
    )
    saved_balance_sheets: list[dict[str, Any]] = []
    for r in saved_bs_rows:
        res = r.result or {}
        as_of_raw = res.get("as_of")
        if not as_of_raw:
            continue
        try:
            as_of_dt = date.fromisoformat(str(as_of_raw))
        except ValueError:
            continue
        if period_start <= as_of_dt <= period_end:
            saved_balance_sheets.append(
                {
                    "report_id": str(r.id),
                    "as_of": as_of_dt.isoformat(),
                    "basis": str(res.get("basis", "accrual")),
                    "balanced": bool(res.get("balanced", True)),
                    "retained_earnings": str(res.get("retained_earnings", "0")),
                    "items": list(res.get("items", [])),
                }
            )

    periods_list = [
        {
            "id": str(p.id),
            "start_date": p.start_date.isoformat(),
            "end_date": p.end_date.isoformat(),
            "locked": bool(p.locked),
            "status": p.status,
        }
        for p in db.scalars(
            select(Period)
            .where(Period.business_id == business_id)
            .order_by(Period.start_date)
            .limit(240)
        )
    ]

    close_tasks = [
        {
            "id": str(t.id),
            "period_id": str(t.period_id),
            "name": sanitize_untrusted_text(t.name, max_length=120)[0],
            "required": bool(t.required),
            "state": t.state,
            "prepared_by": str(t.prepared_by) if t.prepared_by else None,
            "reviewed_by": str(t.reviewed_by) if t.reviewed_by else None,
        }
        for t in db.scalars(
            select(CloseTask).where(CloseTask.business_id == business_id).limit(1000)
        )
    ]

    prepaid_rows = _safe_optional_rows(
        db,
        """
        SELECT COUNT(*) AS cnt FROM app.prepaid_installments
        WHERE business_id = :bid AND due_date <= :end_dt AND journal_id IS NULL
        """,
        {"bid": business_id, "end_dt": period_end},
    )
    pending_prepaid_count = int(prepaid_rows[0]["cnt"]) if prepaid_rows else 0

    bank_rules = [
        {
            "id": str(br.id),
            "name": sanitize_untrusted_text(br.name, max_length=120)[0],
            "contains": br.contains,
            "account_id": str(br.account_id),
            "priority": br.priority,
            "active": bool(br.active),
            "version": br.version,
        }
        for br in db.scalars(
            select(BankRule)
            .where(BankRule.business_id == business_id)
            .order_by(BankRule.priority.desc(), BankRule.id)
            .limit(500)
        )
    ]

    bank_rows = [
        {
            "id": str(r.id),
            "account_id": str(r.account_id),
            "accounting_date": r.accounting_date.isoformat(),
            "description": sanitize_untrusted_text(r.description, max_length=240)[0],
            "amount": str(Decimal(r.amount)),
            "external_id": sanitize_untrusted_text(r.external_id or "", max_length=120)[0],
            "state": r.state,
            "journal_id": str(r.journal_id) if r.journal_id else None,
        }
        for r in db.scalars(
            select(BankRow)
            .where(
                BankRow.business_id == business_id,
                BankRow.accounting_date >= period_start,
                BankRow.accounting_date <= period_end,
            )
            .order_by(BankRow.accounting_date, BankRow.id)
            .limit(max_rows)
        )
    ]

    correction_ids = frozenset(
        str(rid)
        for rid in db.scalars(
            select(BankCorrection.row_id)
            .where(BankCorrection.business_id == business_id)
            .limit(max_rows)
        )
    )

    statements = [
        {
            "id": str(s.id),
            "account_id": str(s.account_id),
            "as_of": s.as_of.isoformat(),
            "closing_balance": str(Decimal(s.closing_balance)),
            "ledger_balance": str(Decimal(s.ledger_balance)),
            "outstanding": list(s.outstanding or []),
        }
        for s in db.scalars(
            select(Statement)
            .where(
                Statement.business_id == business_id,
                Statement.as_of >= period_start,
                Statement.as_of <= period_end,
            )
            .order_by(Statement.as_of, Statement.id)
            .limit(500)
        )
    ]

    parties = {
        str(p.id): {
            "id": str(p.id),
            "name": sanitize_untrusted_text(p.name, max_length=140)[0],
            "roles": list(p.roles or []),
            "active": bool(p.active),
        }
        for p in db.scalars(select(Party).where(Party.business_id == business_id).limit(2000))
    }

    trade_docs = [
        {
            "id": str(d.id),
            "kind": d.kind,
            "number": sanitize_untrusted_text(d.number, max_length=80)[0],
            "party_id": str(d.party_id),
            "accounting_date": d.accounting_date.isoformat(),
            "due_date": d.due_date.isoformat(),
            "control_account_id": str(d.control_account_id),
            "journal_id": str(d.journal_id),
            "total": str(Decimal(d.total)),
            "tax": str(Decimal(d.tax)),
            "lines": list(d.lines or []),
            "disputed": bool(d.disputed),
        }
        for d in db.scalars(
            select(TradeDocument)
            .where(
                TradeDocument.business_id == business_id,
                TradeDocument.accounting_date >= period_start,
                TradeDocument.accounting_date <= period_end,
            )
            .order_by(TradeDocument.accounting_date, TradeDocument.id)
            .limit(max_rows)
        )
    ]

    allocations_by_doc: dict[str, Decimal] = {}
    for doc_id, total_alloc in db.execute(
        select(Allocation.document_id, func.coalesce(func.sum(Allocation.amount), 0))
        .where(
            Allocation.business_id == business_id,
            Allocation.accounting_date <= period_end,
        )
        .group_by(Allocation.document_id)
    ).all():
        allocations_by_doc[str(doc_id)] = Decimal(total_alloc)

    evidence_doc_ids = frozenset(
        str(rid)
        for rid in db.scalars(
            select(EvidenceLink.resource_id).where(
                EvidenceLink.business_id == business_id,
                EvidenceLink.resource_kind == "trade_document",
            )
        )
    )

    payments = [
        {
            "id": str(p.id),
            "kind": p.kind,
            "party_id": str(p.party_id),
            "accounting_date": p.accounting_date.isoformat(),
            "bank_account_id": str(p.bank_account_id),
            "control_account_id": str(p.control_account_id),
            "total": str(Decimal(p.total)),
            "fee_amount": str(Decimal(p.fee_amount)),
            "journal_id": str(p.journal_id),
            "allocations": list(p.allocations or []),
        }
        for p in db.scalars(
            select(Payment)
            .where(
                Payment.business_id == business_id,
                Payment.accounting_date >= period_start,
                Payment.accounting_date <= period_end,
            )
            .order_by(Payment.accounting_date, Payment.id)
            .limit(max_rows)
        )
    ]

    pay_ctrl_rows = _safe_optional_rows(
        db,
        """
        SELECT kill_switch_active, single_payment_limit, dual_approval_threshold
        FROM app.supplier_payment_controls WHERE business_id = :bid
        """,
        {"bid": business_id},
    )
    supplier_payment_controls = (
        {
            "kill_switch_active": bool(pay_ctrl_rows[0]["kill_switch_active"]),
            "single_payment_limit": str(pay_ctrl_rows[0]["single_payment_limit"]),
            "dual_approval_threshold": str(pay_ctrl_rows[0]["dual_approval_threshold"]),
        }
        if pay_ctrl_rows
        else {}
    )

    beneficiaries = {
        str(r["id"]): {
            "id": str(r["id"]),
            "supplier_name": sanitize_untrusted_text(str(r["supplier_name"]), max_length=120)[0],
            "out_of_band_verified": bool(r["out_of_band_verified"]),
        }
        for r in _safe_optional_rows(
            db,
            """
            SELECT id, supplier_name, out_of_band_verified
            FROM app.supplier_beneficiaries WHERE business_id = :bid LIMIT 500
            """,
            {"bid": business_id},
        )
    }

    supplier_payments = [
        {
            "id": str(r["id"]),
            "bill_reference": sanitize_untrusted_text(str(r["bill_reference"]), max_length=80)[0],
            "beneficiary_id": str(r["beneficiary_id"]),
            "amount": str(r["amount"]),
            "state": str(r["state"]),
            "requires_dual_approval": bool(r["requires_dual_approval"]),
            "prepared_by": str(r["prepared_by"]),
            "first_approved_by": str(r["first_approved_by"]) if r["first_approved_by"] else None,
            "final_approved_by": str(r["final_approved_by"]) if r["final_approved_by"] else None,
        }
        for r in _safe_optional_rows(
            db,
            """
            SELECT id, bill_reference, beneficiary_id, amount, state,
                   requires_dual_approval, prepared_by, first_approved_by, final_approved_by
            FROM app.supplier_payment_executions
            WHERE business_id = :bid
              AND created_at::date BETWEEN :start_dt AND :end_dt
            LIMIT 500
            """,
            {"bid": business_id, "start_dt": period_start, "end_dt": period_end},
        )
    ]

    approved_memories = [
        {
            "id": str(r["id"]),
            "memory_key": str(r["memory_key"]),
            "title": str(r["title"]),
            "accounting_framework": str(r["accounting_framework"]),
            "category": str(r["category"]),
            "rule_condition": str(r["rule_condition"]),
            "account_code": str(r["account_code"]),
            "account_name": str(r["account_name"]),
            "materiality_threshold": str(r["materiality_threshold"]),
        }
        for r in _safe_optional_rows(
            db,
            """
            SELECT id, memory_key, title, accounting_framework, category,
                   rule_condition, account_code, account_name, materiality_threshold
            FROM app.ai_accounting_memories
            WHERE business_id = :bid AND status = 'approved'
            ORDER BY version DESC LIMIT 50
            """,
            {"bid": business_id},
        )
    ]

    policy_history = [
        {
            "version": p.version,
            "effective_date": p.effective_date.isoformat(),
            "basis": p.basis,
            "jurisdiction": p.jurisdiction,
        }
        for p in db.scalars(
            select(PolicyHistory)
            .where(PolicyHistory.business_id == business_id)
            .order_by(PolicyHistory.version.desc())
            .limit(20)
        )
    ]

    evidence_summaries = [
        {
            "id": str(e.id),
            "filename": e.filename,
            "status": e.status,
            "snippet": (e.raw_text or "")[:240],
        }
        for e in db.scalars(
            select(Evidence)
            .where(Evidence.business_id == business_id)
            .order_by(Evidence.id.desc())
            .limit(30)
        )
    ]

    return AuditDataset(
        run_id=run_id,
        business_id=str(business_id),
        business_name=business.name,
        currency=business.currency,
        currency_precision=business.currency_precision,
        basis=business.basis,
        timezone=business.timezone,
        fiscal_month=business.fiscal_month,
        jurisdiction=business.jurisdiction,
        period_start=period_start,
        period_end=period_end,
        accounts=accounts,
        opening_balances=opening_balances,
        period_movements=period_movements,
        closing_balances=closing_balances,
        journals=journals,
        journal_lines_by_journal=dict(lines_by_journal),
        saved_balance_sheets=saved_balance_sheets,
        computed_balance_sheet=computed_bs,
        control_tieouts=control_tieouts,
        tax_tieouts=tax_tieouts,
        periods=periods_list,
        close_tasks=close_tasks,
        pending_prepaid_count=pending_prepaid_count,
        bank_rules=bank_rules,
        bank_rows=bank_rows,
        bank_corrections_row_ids=correction_ids,
        statements=statements,
        trade_documents=trade_docs,
        parties=parties,
        payments=payments,
        allocations_by_doc=allocations_by_doc,
        evidence_doc_ids=evidence_doc_ids,
        all_journal_ids=all_journal_ids,
        posted_journal_ids=posted_journal_ids,
        supplier_payment_controls=supplier_payment_controls,
        supplier_beneficiaries=beneficiaries,
        supplier_payments=supplier_payments,
        approved_memories=approved_memories,
        policy_history=policy_history,
        evidence_summaries=evidence_summaries,
    )
