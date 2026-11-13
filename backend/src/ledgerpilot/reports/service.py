"""Ledger-backed reports and allocation-based cash recognition with source drill-down."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account, Journal, JournalLine
from ledgerpilot.accounting.money import allocate
from ledgerpilot.identity.security import now
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.query_limits import bounded_rows, bounded_scalars
from ledgerpilot.tenancy.models import Business
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.models import Allocation, Loan, LoanMovement, Payment, TradeDocument
from ledgerpilot.workspace.models import AdvanceApplication, ReportRun
from ledgerpilot.workspace.opening_cash import OpeningSources


def context(
    db: Session, bid: UUID, as_of: date, basis: str, business: Business | None = None
) -> dict[str, Any]:
    business = business or get_business(db, bid)
    return {
        "report_id": str(uuid4()),
        "as_of": as_of,
        "basis": basis,
        "currency": business.currency,
        "timezone": business.timezone,
        "book": "local",
        "policy": "reviewed-source-amounts-no-statutory-filing",
    }


def balances(
    db: Session, bid: UUID, as_of: date, account_ids: list[UUID] | None = None
) -> dict[UUID, Decimal]:
    account_filter = () if account_ids is None else (JournalLine.account_id.in_(account_ids),)
    return {
        aid: amount
        for aid, amount in bounded_rows(
            db,
            select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
            .join(
                Journal,
                (Journal.id == JournalLine.journal_id)
                & (Journal.business_id == JournalLine.business_id),
            )
            .where(
                Journal.business_id == bid,
                Journal.status == "posted",
                Journal.accounting_date <= as_of,
                *account_filter,
            )
            .group_by(JournalLine.account_id),
        )
    }


def balance_snapshots(
    db: Session, bid: UUID, dates: list[date], account_ids: list[UUID] | None = None
) -> dict[date, dict[UUID, Decimal]]:
    """Read posted balances for bounded checkpoints in one grouped query."""
    dates = sorted(set(dates))
    if len(dates) > 14:
        raise DomainError(
            "ERR_QUOTA", "The pilot report supports at most 14 date checkpoints.", 413
        )
    values: dict[date, dict[UUID, Decimal]] = {day: {} for day in dates}
    if not dates:
        return values
    account_filter = () if account_ids is None else (JournalLine.account_id.in_(account_ids),)
    amounts = [
        func.coalesce(
            func.sum(JournalLine.debit - JournalLine.credit).filter(Journal.accounting_date <= day),
            0,
        )
        for day in dates
    ]
    for aid, *totals in bounded_rows(
        db,
        select(JournalLine.account_id, *amounts)
        .join(
            Journal,
            (Journal.id == JournalLine.journal_id)
            & (Journal.business_id == JournalLine.business_id),
        )
        .where(
            Journal.business_id == bid,
            Journal.status == "posted",
            Journal.accounting_date <= dates[-1],
            *account_filter,
        )
        .group_by(JournalLine.account_id),
    ):
        for day, amount in zip(dates, totals, strict=True):
            values[day][aid] = amount
    return values


@dataclass
class CashSources:
    business: Business
    accounts: list[Account]
    documents: list[TradeDocument]
    applications: list[tuple[UUID, date, Decimal]]
    allocations: list[tuple[UUID, date, Decimal]]
    payments: list[Payment]
    openings: OpeningSources

    @classmethod
    def load(
        cls, db: Session, bid: UUID, end: date, business: Business, accounts: list[Account]
    ) -> "CashSources":
        return cls(
            business,
            accounts,
            bounded_scalars(
                db,
                select(TradeDocument).where(
                    TradeDocument.business_id == bid, TradeDocument.accounting_date <= end
                ),
            ),
            [
                (did, day, amount)
                for did, day, amount in bounded_rows(
                    db,
                    select(
                        AdvanceApplication.document_id,
                        AdvanceApplication.accounting_date,
                        func.sum(AdvanceApplication.amount),
                    )
                    .where(
                        AdvanceApplication.business_id == bid,
                        AdvanceApplication.accounting_date <= end,
                    )
                    .group_by(AdvanceApplication.document_id, AdvanceApplication.accounting_date),
                )
            ],
            [
                (did, day, amount)
                for did, day, amount in bounded_rows(
                    db,
                    select(
                        Allocation.document_id,
                        Allocation.accounting_date,
                        func.sum(Allocation.amount),
                    )
                    .where(Allocation.business_id == bid, Allocation.accounting_date <= end)
                    .group_by(Allocation.document_id, Allocation.accounting_date),
                )
            ],
            bounded_scalars(
                db,
                select(Payment).where(
                    Payment.business_id == bid,
                    Payment.accounting_date <= end,
                    Payment.kind.in_(["customer_refund", "vendor_refund"]),
                ),
            ),
            OpeningSources.load(db, bid, end, business.currency_precision),
        )


@dataclass
class FinancialInputs:
    """Explicit read-only inputs for one calculation; never cached on pooled sessions."""

    business: Business
    accounts: list[Account]
    ledger: dict[date, dict[UUID, Decimal]]
    cash: CashSources | None

    @classmethod
    def load(cls, db: Session, bid: UUID, dates: list[date], basis: str) -> "FinancialInputs":
        business = get_business(db, bid)
        accounts = bounded_scalars(
            db, select(Account).where(Account.business_id == bid).order_by(Account.code)
        )
        return cls(
            business,
            accounts,
            balance_snapshots(db, bid, dates),
            CashSources.load(db, bid, max(dates), business, accounts) if basis == "cash" else None,
        )


def cash_adjustments(
    db: Session,
    bid: UUID,
    as_of: date,
    sources: CashSources | None = None,
    ledger: dict[UUID, Decimal] | None = None,
) -> dict[UUID, Decimal]:
    """Convert tracked trade accruals to realized cash, preserving exact remainder.

    Explicit manual journals remain approved exceptions. Refunds recognize the
    original payment's categories on the refund date. Tax is allocated separately.
    """
    if sources is None:
        business = get_business(db, bid)
        accounts = bounded_scalars(db, select(Account).where(Account.business_id == bid))
        sources = CashSources.load(db, bid, as_of, business, accounts)
    business = sources.business
    docs = [doc for doc in sources.documents if doc.accounting_date <= as_of]
    realized: dict[UUID, Decimal] = defaultdict(Decimal)
    for did, day, amount in sources.applications:
        if day <= as_of:
            realized[did] += amount
    for did, day, amount in sources.allocations:
        if day <= as_of:
            realized[did] += amount
    payments = [payment for payment in sources.payments if payment.accounting_date <= as_of]
    for payment in payments:
        if payment.kind in {"customer_refund", "vendor_refund"}:
            for allocation in payment.allocations:
                if not allocation.get("credit_note_id"):
                    raise DomainError(
                        "ERR_CASH_POLICY", "A refund requires its credited source.", 409
                    )
                realized[UUID(allocation["credit_note_id"])] -= Decimal(allocation["amount"])
    from ledgerpilot.workspace.opening_cash import adjustments

    skip, opening_changes = adjustments(db, bid, as_of, docs, realized, payments, sources.openings)
    adjustment: dict[UUID, Decimal] = defaultdict(Decimal, opening_changes)
    for doc in docs:
        if doc.kind == "sales_receipt" or doc.id in skip:
            continue
        sale = doc.kind in {"invoice", "customer_credit"}
        credit = doc.kind in {"customer_credit", "vendor_credit"}
        target = realized[doc.id]
        # Cash refunds recognize their own credit categories and tax components.
        accrual = -doc.total if credit else doc.total
        difference = target - accrual
        if not difference:
            continue
        components: list[tuple[UUID, Decimal]] = []
        for row in doc.lines:
            components.append((UUID(row["account_id"]), Decimal(row["amount"])))
            if Decimal(row["tax_amount"]):
                components.append((UUID(row["tax_account_id"]), Decimal(row["tax_amount"])))
        parts = allocate(abs(difference), [v for _, v in components], business.currency_precision)
        sign = Decimal(1) if difference > 0 else Decimal(-1)
        for (aid, _), part in zip(components, parts, strict=True):
            adjustment[aid] += part * sign * (-1 if sale else 1)
        adjustment[doc.control_account_id] += difference * (1 if sale else -1)
    # Avoid silently treating unmatched historic control entries as recognized cash.
    controls = {a.id for a in sources.accounts if a.control in {"ar", "ap"}}
    if ledger is None:
        ledger = balances(db, bid, as_of)
    if any(ledger.get(aid, Decimal(0)) + adjustment.get(aid, Decimal(0)) != 0 for aid in controls):
        raise DomainError(
            "ERR_CASH_POLICY", "Unmatched AR/AP entries require an approved cash exception.", 409
        )
    return adjustment


def financial(
    db: Session,
    bid: UUID,
    start: date,
    end: date,
    basis: str,
    kind: str,
    *,
    inputs: FinancialInputs | None = None,
) -> dict[str, Any]:
    if end < start or start == date.min or end == date.max:
        raise DomainError("ERR_DATE_RANGE", "Report dates must be ordered.")
    if inputs is None:
        inputs = FinancialInputs.load(db, bid, [end, start - timedelta(days=1)], basis)
    accounts = inputs.accounts
    closing = inputs.ledger[end].copy()
    opening = inputs.ledger[start - timedelta(days=1)].copy()
    if basis == "cash":
        for aid, value in cash_adjustments(db, bid, end, inputs.cash, inputs.ledger[end]).items():
            closing[aid] = closing.get(aid, Decimal(0)) + value
        for aid, value in cash_adjustments(
            db,
            bid,
            start - timedelta(days=1),
            inputs.cash,
            inputs.ledger[start - timedelta(days=1)],
        ).items():
            opening[aid] = opening.get(aid, Decimal(0)) + value
    rows = []
    income, expense = Decimal(0), Decimal(0)
    for account in accounts:
        value = closing.get(account.id, Decimal(0))
        if account.kind in {"income", "expense"}:
            movement = value - opening.get(account.id, Decimal(0))
            if account.kind == "income":
                income -= movement
            else:
                expense += movement
            if kind == "profit-loss":
                rows.append(
                    {
                        "account_id": str(account.id),
                        "code": account.code,
                        "name": account.name,
                        "kind": account.kind,
                        "amount": str(-movement if account.kind == "income" else movement),
                    }
                )
        elif kind == "balance-sheet":
            rows.append(
                {
                    "account_id": str(account.id),
                    "code": account.code,
                    "name": account.name,
                    "kind": account.kind,
                    "amount": str(value if account.kind == "asset" else -value),
                }
            )
    result = {
        **context(db, bid, end, basis, inputs.business),
        "start_date": start,
        "kind": kind,
        "items": rows,
        "income": str(income),
        "expense": str(expense),
        "net_profit": str(income - expense),
        "opening_cash_policy_ids": sorted(db.info.get("opening_cash_policy_ids", set())),
    }
    if kind == "balance-sheet":
        retained = -sum(
            (closing.get(a.id, Decimal(0)) for a in accounts if a.kind in {"income", "expense"}),
            Decimal(0),
        )
        result["retained_earnings"] = str(retained)
        result["balanced"] = sum(closing.values(), Decimal(0)) == 0
    return result


def aging(
    db: Session,
    bid: UUID,
    as_of: date,
    kind: str,
    *,
    outstanding: dict[UUID, Decimal] | None = None,
) -> dict[str, Any]:
    due = outstanding if outstanding is not None else repo.due(db, bid, as_of)
    rows = list(
        db.scalars(
            select(TradeDocument)
            .where(
                TradeDocument.business_id == bid,
                TradeDocument.kind == kind,
                TradeDocument.accounting_date <= as_of,
            )
            .order_by(TradeDocument.due_date, TradeDocument.id)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise DomainError(
            "ERR_QUOTA", "Narrow the pilot report scope or request an export job.", 413
        )
    items: list[dict[str, Any]] = []
    for row in rows:
        amount = due.get(row.id, Decimal(0))
        if not amount:
            continue
        days = (as_of - row.due_date).days
        bucket = (
            "current"
            if days <= 0
            else "1-30"
            if days <= 30
            else "31-60"
            if days <= 60
            else "61-90"
            if days <= 90
            else "90+"
        )
        items.append(
            {
                "document_id": str(row.id),
                "party_id": str(row.party_id),
                "number": row.number,
                "due_date": row.due_date,
                "disputed": row.disputed,
                "bucket": "credit" if amount < 0 else bucket,
                "outstanding": str(amount),
            }
        )
    return {
        **context(db, bid, as_of, "operational_accrual"),
        "items": items,
        "total": str(sum((Decimal(i["outstanding"]) for i in items), Decimal(0))),
    }


def controls(
    db: Session, bid: UUID, as_of: date, *, outstanding: dict[UUID, Decimal] | None = None
) -> dict[str, Any]:
    ledger = balances(db, bid, as_of)
    source: dict[UUID, Decimal] = defaultdict(Decimal)
    due = outstanding if outstanding is not None else repo.due(db, bid, as_of)
    for item in bounded_scalars(
        db,
        select(TradeDocument).where(
            TradeDocument.business_id == bid,
            TradeDocument.kind.in_(["invoice", "bill"]),
            TradeDocument.accounting_date <= as_of,
        ),
    ):
        source[item.control_account_id] += due[item.id] * (1 if item.kind == "invoice" else -1)
    movements = (
        select(
            Loan.control_account_id,
            Loan.direction,
            func.sum(
                case(
                    (
                        LoanMovement.kind.in_(["drawdown", "opening", "opening_increase"]),
                        LoanMovement.principal,
                    ),
                    else_=-LoanMovement.principal,
                )
            ),
        )
        .join(
            LoanMovement,
            (Loan.id == LoanMovement.loan_id) & (Loan.business_id == LoanMovement.business_id),
        )
        .where(
            Loan.business_id == bid,
            LoanMovement.accounting_date <= as_of,
        )
        .group_by(Loan.control_account_id, Loan.direction)
    )
    for aid, direction, amount in bounded_rows(db, movements):
        source[UUID(str(aid))] += Decimal(str(amount)) * (-1 if direction == "payable" else 1)
    items: list[dict[str, Any]] = []
    for account in bounded_scalars(
        db,
        select(Account).where(
            Account.business_id == bid,
            Account.control.in_(["ar", "ap", "loan_payable", "loan_receivable"]),
        ),
    ):
        gl, sl = ledger.get(account.id, Decimal(0)), source[account.id]
        items.append(
            {
                "account_id": str(account.id),
                "control": account.control,
                "ledger_balance": str(gl),
                "subledger_balance": str(sl),
                "difference": str(gl - sl),
                "balanced": gl == sl,
            }
        )
    return {
        **context(db, bid, as_of, "accrual"),
        "items": items,
        "balanced": all(i["balanced"] for i in items),
    }


def profit_variance(db: Session, bid: UUID, current_id: UUID, previous_id: UUID) -> dict[str, Any]:
    reports = {
        report.id: report.result
        for report in db.scalars(
            select(ReportRun).where(
                ReportRun.business_id == bid,
                ReportRun.id.in_([current_id, previous_id]),
                ReportRun.kind == "profit-loss",
            )
        )
    }
    if current_id not in reports or previous_id not in reports:
        raise DomainError("ERR_NOT_FOUND", "Profit and loss report not found.", 404)
    current, previous = reports[current_id], reports[previous_id]
    current_start, current_end = (date.fromisoformat(current[k]) for k in ("start_date", "as_of"))
    previous_start, previous_end = (
        date.fromisoformat(previous[k]) for k in ("start_date", "as_of")
    )
    if (
        previous_end >= current_start
        or current_end - current_start != previous_end - previous_start
        or any(current[k] != previous[k] for k in ("basis", "currency", "book", "policy"))
    ):
        raise DomainError(
            "ERR_REPORT_COMPARISON",
            "Choose non-overlapping periods of equal length with matching report settings.",
            422,
        )
    contributions: dict[str, Decimal] = defaultdict(Decimal)
    names = {}
    for report, direction in ((previous, -1), (current, 1)):
        for row in report["items"]:
            aid = row["account_id"]
            names[aid] = row["name"]
            sign = 1 if row["kind"] == "income" else -1
            contributions[aid] += Decimal(row["amount"]) * sign * direction
    ranked = sorted(contributions, key=lambda aid: (-abs(contributions[aid]), aid))
    return {
        "current_report_id": current_id,
        "previous_report_id": previous_id,
        "currency": current["currency"],
        "basis": current["basis"],
        "net_profit_change": str(Decimal(current["net_profit"]) - Decimal(previous["net_profit"])),
        "income_change": str(Decimal(current["income"]) - Decimal(previous["income"])),
        "expense_change": str(Decimal(current["expense"]) - Decimal(previous["expense"])),
        "drivers": [
            {"account_id": aid, "name": names[aid], "contribution": str(contributions[aid])}
            for aid in ranked[:5]
            if contributions[aid]
        ],
    }


def evaluate_forecast(db: Session, bid: UUID, report_id: UUID) -> dict[str, Any]:
    stored = db.scalar(
        select(ReportRun).where(
            ReportRun.business_id == bid,
            ReportRun.id == report_id,
            ReportRun.kind == "deterministic_cash_plan",
        )
    )
    if stored is None:
        raise DomainError("ERR_NOT_FOUND", "Saved cash forecast not found.", 404)
    report = stored.result
    if report.get("scenario") or not report.get("cash_account_ids"):
        raise DomainError(
            "ERR_FORECAST_EVALUATION",
            "Choose a baseline forecast saved with its cash account selection.",
            422,
        )
    timezone = ZoneInfo(report["timezone"])
    as_of = date.fromisoformat(report["as_of"])
    if stored.created_at.astimezone(timezone).date() != as_of:
        raise DomainError(
            "ERR_FORECAST_EVALUATION",
            "The baseline must have been saved on its forecast date.",
            422,
        )
    today = now().astimezone(timezone).date()
    completed = [
        (date.fromisoformat(week["end_date"]), week)
        for week in report["weeks"]
        if date.fromisoformat(week["end_date"]) < today
    ]
    accounts = [UUID(value) for value in report["cash_account_ids"]]
    observed = balance_snapshots(db, bid, [day for day, _ in completed], accounts)
    rows = []
    errors: list[Decimal] = []
    for day, week in completed:
        forecast_cash = Decimal(week["closing_cash"])
        actual_cash = sum(observed[day].values(), Decimal(0))
        difference = actual_cash - forecast_cash
        errors.append(abs(difference))
        rows.append(
            {
                "end_date": day,
                "forecast_cash": str(forecast_cash),
                "ledger_cash": str(actual_cash),
                "difference": str(difference),
            }
        )
    return {
        "report_id": stored.id,
        "method": "ledger_cash_error_v1",
        "currency": report["currency"],
        "weeks": rows,
        "mean_absolute_error": str(sum(errors, Decimal(0)) / len(errors)) if errors else None,
    }


def forecast(
    db: Session,
    bid: UUID,
    as_of: date,
    bank_ids: list[UUID],
    scenario: tuple[int, int] | None = None,
) -> dict[str, Any]:
    from ledgerpilot.shared.operations import load_accounts, validate_account

    if as_of > date.max - timedelta(days=91):
        raise DomainError("ERR_DATE_RANGE", "Forecast needs 91 days after its start date.", 422)
    if not bank_ids or len(bank_ids) != len(set(bank_ids)):
        raise DomainError("ERR_FORECAST", "Select unique cash accounts.")
    accounts = load_accounts(db, bid, bank_ids)
    for aid in bank_ids:
        validate_account(accounts.get(aid), {"asset"}, plain=True)
    current = balances(db, bid, as_of, bank_ids)
    cash = sum((current.get(aid, Decimal(0)) for aid in bank_ids), Decimal(0))
    docs = bounded_scalars(
        db,
        select(TradeDocument)
        .where(
            TradeDocument.business_id == bid,
            TradeDocument.kind.in_(["invoice", "bill"]),
            TradeDocument.accounting_date <= as_of,
            TradeDocument.due_date.between(as_of + timedelta(days=1), as_of + timedelta(days=91)),
            TradeDocument.disputed.is_(False),
        )
        .order_by(TradeDocument.due_date, TradeDocument.id),
    )
    due = repo.due_snapshots(db, bid, [as_of], [d.id for d in docs])[as_of]
    business = get_business(db, bid)
    quantum = Decimal(1).scaleb(-business.currency_precision)
    weekly: dict[int, list[TradeDocument]] = defaultdict(list)
    for document in docs:
        if due[document.id] > 0:
            weekly[((document.due_date - as_of).days - 1) // 7].append(document)
    rows = []
    first_shortfall = None
    for index in range(13):
        start = as_of + timedelta(days=1 + index * 7)
        end = start + timedelta(days=6)
        receipts, payments = Decimal(0), Decimal(0)
        sources = []
        for d in weekly[index]:
            value = due[d.id]
            if d.kind == "invoice":
                receipts += value
            else:
                payments += value
            sources.append(str(d.id))
        if scenario is not None:
            receipts = (receipts * scenario[0] / 100).quantize(quantum, rounding=ROUND_HALF_EVEN)
            payments = (payments * scenario[1] / 100).quantize(quantum, rounding=ROUND_HALF_EVEN)
        cash += receipts - payments
        if cash < 0 and first_shortfall is None:
            first_shortfall = end
        rows.append(
            {
                "week": index + 1,
                "start_date": start,
                "end_date": end,
                "receipts": str(receipts),
                "payments": str(payments),
                "closing_cash": str(cash),
                "shortfall": cash < 0,
                "document_ids": sources,
            }
        )
    result = {
        **context(db, bid, as_of, "deterministic_cash_plan", business),
        "cash_account_ids": [str(aid) for aid in bank_ids],
        "weeks": rows,
        "confidence": "low",
        "bands": None,
        "assumptions": [
            "Selected cash account balances only; no overdraft limit assumed.",
            "Undisputed issued invoices and bills settle on stated due dates.",
            "Overdue items, unissued sales, payroll, tax remittances "
            "and loan schedules are excluded.",
            "No calibrated uncertainty bands are available; this is not a statistical prediction.",
        ],
    }
    if scenario is not None:
        result["scenario"] = {"receipt_percent": scenario[0], "payment_percent": scenario[1]}
        result["first_shortfall"] = first_shortfall
        result["assumptions"].append(
            "Scenario percentages scale weekly receipts and payments; each total is rounded to "
            "the currency precision. Opening cash and due dates are unchanged."
        )
    return result


def tax_support(db: Session, bid: UUID, start: date, end: date) -> dict[str, Any]:
    from ledgerpilot.trade.models import Operation
    from ledgerpilot.workspace.models import ImportBatch

    if end < start or start == date.min or end == date.max:
        raise DomainError("ERR_DATE_RANGE", "Report dates must be ordered.")
    opening, closing = balances(db, bid, start - timedelta(days=1)), balances(db, bid, end)
    source: dict[UUID, Decimal] = defaultdict(Decimal)
    accounts = list(
        db.scalars(
            select(Account)
            .where(Account.business_id == bid, Account.control == "tax")
            .order_by(Account.code)
        )
    )
    baseline = db.scalar(
        select(ImportBatch)
        .where(
            ImportBatch.business_id == bid,
            ImportBatch.mode.in_(["journal", "trade"]),
            ImportBatch.cutover_date <= end,
        )
        .order_by(
            ImportBatch.cutover_date.desc(), ImportBatch.created_at.desc(), ImportBatch.id.desc()
        )
        .limit(1)
    )
    cutoff = baseline.cutover_date if baseline else date.min
    if baseline:
        for account in accounts:
            source[account.id] = Decimal(baseline.expected_trial.get(account.code, "0"))
    documents = bounded_scalars(
        db,
        select(TradeDocument).where(
            TradeDocument.business_id == bid,
            TradeDocument.accounting_date <= end,
            TradeDocument.accounting_date > cutoff,
        ),
    )
    references: dict[UUID, list[str]] = defaultdict(list)
    for doc in documents:
        sale = doc.kind in {"invoice", "sales_receipt", "customer_credit"}
        credit = doc.kind in {"customer_credit", "vendor_credit"}
        for row in doc.lines:
            if row["tax_account_id"] and Decimal(row["tax_amount"]):
                aid = UUID(row["tax_account_id"])
                source[aid] += Decimal(row["tax_amount"]) * (-1 if sale != credit else 1)
                references[aid].append(str(doc.id))
    for operation in bounded_scalars(
        db,
        select(Operation).where(
            Operation.business_id == bid,
            Operation.action == "tax.remitted",
        ),
    ):
        if cutoff < date.fromisoformat(operation.result["accounting_date"]) <= end:
            aid = UUID(operation.result["tax_account_id"])
            source[aid] += Decimal(operation.result["amount"])
            references[aid].append(str(operation.id))
    items: list[dict[str, Any]] = []
    for account in accounts:
        value = closing.get(account.id, Decimal(0))
        items.append(
            {
                "account_id": str(account.id),
                "name": account.name,
                "kind": account.kind,
                "opening_debit_minus_credit": str(opening.get(account.id, Decimal(0))),
                "closing_debit_minus_credit": str(value),
                "source_balance": str(source[account.id]),
                "difference": str(value - source[account.id]),
                "source_ids": references[account.id],
            }
        )
    return {
        **context(db, bid, end, "accrual"),
        "kind": "tax-support",
        "start_date": start,
        "items": items,
        "statutory_filing": False,
        "source_baseline_id": str(baseline.id) if baseline else None,
        "balanced": all(Decimal(i["difference"]) == 0 for i in items),
    }


def dashboard(
    db: Session,
    bid: UUID,
    start: date,
    end: date,
    basis: str,
    bank_ids: list[UUID],
) -> dict[str, Any]:
    if (end - start).days > 366 or end < start or start == date.min or end == date.max:
        raise DomainError("ERR_DATE_RANGE", "Dashboard range must be ordered and at most 366 days.")
    from calendar import monthrange

    periods = []
    day = start
    while day <= end:
        last = min(end, date(day.year, day.month, monthrange(day.year, day.month)[1]))
        periods.append((day, last))
        day = last + timedelta(days=1)
    inputs = FinancialInputs.load(
        db, bid, [start - timedelta(days=1), *(last for _, last in periods)], basis
    )
    totals = financial(db, bid, start, end, basis, "profit-loss", inputs=inputs)
    trends = []
    for day, last in periods:
        part = financial(db, bid, day, last, basis, "profit-loss", inputs=inputs)
        trends.append(
            {
                "start_date": day,
                "end_date": last,
                "income": part["income"],
                "expense": part["expense"],
                "net_profit": part["net_profit"],
            }
        )
    current = inputs.ledger[end]
    from ledgerpilot.shared.operations import validate_account

    if len(bank_ids) != len(set(bank_ids)):
        raise DomainError("ERR_ACCOUNT", "Cash accounts cannot be selected twice.")
    accounts = {a.id: a for a in inputs.accounts}
    for aid in bank_ids:
        validate_account(accounts.get(aid), {"asset"}, plain=True)
    outstanding = repo.due(db, bid, end)
    return {
        **totals,
        "kind": "dashboard",
        "trend": trends,
        "cash_balance": str(sum((current.get(aid, Decimal(0)) for aid in bank_ids), Decimal(0))),
        "cash_account_ids": [str(aid) for aid in bank_ids],
        "receivables": aging(db, bid, end, "invoice", outstanding=outstanding),
        "payables": aging(db, bid, end, "bill", outstanding=outstanding),
        "controls": controls(db, bid, end, outstanding=outstanding),
    }
