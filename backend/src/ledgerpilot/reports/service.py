"""Ledger-backed reports and allocation-based cash recognition with source drill-down."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account, Journal, JournalLine
from ledgerpilot.accounting.money import allocate
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.models import Allocation, Loan, LoanMovement, Payment, TradeDocument
from ledgerpilot.workspace.models import AdvanceApplication


def context(db: Session, bid: UUID, as_of: date, basis: str) -> dict[str, Any]:
    business = get_business(db, bid)
    return {
        "report_id": str(uuid4()),
        "as_of": as_of,
        "basis": basis,
        "currency": business.currency,
        "timezone": business.timezone,
        "book": "local",
        "policy": "reviewed-source-amounts-no-statutory-filing",
    }


def balances(db: Session, bid: UUID, as_of: date) -> dict[UUID, Decimal]:
    return dict(
        db.execute(
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
            )
            .group_by(JournalLine.account_id)
        ).all()
    )


def cash_adjustments(db: Session, bid: UUID, as_of: date) -> dict[UUID, Decimal]:
    """Convert tracked trade accruals to realized cash, preserving exact remainder.

    Explicit manual journals remain approved exceptions. Refunds recognize the
    original payment's categories on the refund date. Tax is allocated separately.
    """
    business = get_business(db, bid)
    docs = list(
        db.scalars(
            select(TradeDocument).where(
                TradeDocument.business_id == bid,
                TradeDocument.accounting_date <= as_of,
            )
        )
    )
    realized: dict[UUID, Decimal] = defaultdict(Decimal)
    for did, amount in db.execute(
        select(AdvanceApplication.document_id, func.sum(AdvanceApplication.amount))
        .where(AdvanceApplication.business_id == bid, AdvanceApplication.accounting_date <= as_of)
        .group_by(AdvanceApplication.document_id)
    ):
        realized[did] += amount
    for did, amount in db.execute(
        select(Allocation.document_id, func.sum(Allocation.amount))
        .where(Allocation.business_id == bid, Allocation.accounting_date <= as_of)
        .group_by(Allocation.document_id)
    ):
        realized[did] += amount
    payments = list(
        db.scalars(
            select(Payment).where(
                Payment.business_id == bid,
                Payment.accounting_date <= as_of,
            )
        )
    )
    for payment in payments:
        if payment.kind in {"customer_refund", "vendor_refund"}:
            for allocation in payment.allocations:
                if not allocation.get("credit_note_id"):
                    raise DomainError(
                        "ERR_CASH_POLICY", "A refund requires its credited source.", 409
                    )
                realized[UUID(allocation["credit_note_id"])] -= Decimal(allocation["amount"])
    from ledgerpilot.workspace.opening_cash import adjustments

    skip, opening_changes = adjustments(db, bid, as_of, docs, realized, payments)
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
    controls = {
        a.id
        for a in db.scalars(
            select(Account).where(
                Account.business_id == bid,
                Account.control.in_(["ar", "ap"]),
            )
        )
    }
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
) -> dict[str, Any]:
    if end < start or start == date.min or end == date.max:
        raise DomainError("ERR_DATE_RANGE", "Report dates must be ordered.")
    accounts = list(
        db.scalars(select(Account).where(Account.business_id == bid).order_by(Account.code))
    )
    closing = balances(db, bid, end)
    opening = balances(db, bid, start - timedelta(days=1))
    if basis == "cash":
        for aid, value in cash_adjustments(db, bid, end).items():
            closing[aid] = closing.get(aid, Decimal(0)) + value
        for aid, value in cash_adjustments(db, bid, start - timedelta(days=1)).items():
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
        **context(db, bid, end, basis),
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


def aging(db: Session, bid: UUID, as_of: date, kind: str) -> dict[str, Any]:
    due = repo.due(db, bid, as_of)
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


def controls(db: Session, bid: UUID, as_of: date) -> dict[str, Any]:
    ledger = balances(db, bid, as_of)
    source: dict[UUID, Decimal] = defaultdict(Decimal)
    due = repo.due(db, bid, as_of)
    for item in db.scalars(
        select(TradeDocument).where(
            TradeDocument.business_id == bid,
            TradeDocument.kind.in_(["invoice", "bill"]),
            TradeDocument.accounting_date <= as_of,
        )
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
    for aid, direction, amount in db.execute(movements):
        source[UUID(str(aid))] += Decimal(str(amount)) * (-1 if direction == "payable" else 1)
    items: list[dict[str, Any]] = []
    for account in db.scalars(
        select(Account).where(
            Account.business_id == bid,
            Account.control.in_(["ar", "ap", "loan_payable", "loan_receivable"]),
        )
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


def forecast(db: Session, bid: UUID, as_of: date, bank_ids: list[UUID]) -> dict[str, Any]:
    from ledgerpilot.shared.operations import account

    if as_of > date.max - timedelta(days=91):
        raise DomainError("ERR_DATE_RANGE", "Forecast needs 91 days after its start date.", 422)
    if not bank_ids or len(bank_ids) != len(set(bank_ids)):
        raise DomainError("ERR_FORECAST", "Select unique cash accounts.")
    for aid in bank_ids:
        account(db, bid, aid, {"asset"}, plain=True)
    current = balances(db, bid, as_of)
    cash = sum((current.get(aid, Decimal(0)) for aid in bank_ids), Decimal(0))
    due = repo.due(db, bid, as_of)
    docs = list(
        db.scalars(
            select(TradeDocument).where(
                TradeDocument.business_id == bid,
                TradeDocument.kind.in_(["invoice", "bill"]),
                TradeDocument.accounting_date <= as_of,
            )
        )
    )
    rows = []
    for index in range(13):
        start = as_of + timedelta(days=1 + index * 7)
        end = start + timedelta(days=6)
        receipts, payments = Decimal(0), Decimal(0)
        sources = []
        for d in docs:
            if d.disputed or not start <= d.due_date <= end or due[d.id] <= 0:
                continue
            value = due[d.id]
            if d.kind == "invoice":
                receipts += value
            else:
                payments += value
            sources.append(str(d.id))
        cash += receipts - payments
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
    return {
        **context(db, bid, as_of, "deterministic_cash_plan"),
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
    documents = list(
        db.scalars(
            select(TradeDocument).where(
                TradeDocument.business_id == bid,
                TradeDocument.accounting_date <= end,
                TradeDocument.accounting_date > cutoff,
            )
        )
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
    for operation in db.scalars(
        select(Operation).where(
            Operation.business_id == bid,
            Operation.action == "tax.remitted",
        )
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
    if (end - start).days > 366 or end < start:
        raise DomainError("ERR_DATE_RANGE", "Dashboard range must be ordered and at most 366 days.")
    from calendar import monthrange

    totals = financial(db, bid, start, end, basis, "profit-loss")
    trends = []
    day = start
    while day <= end:
        last = min(end, date(day.year, day.month, monthrange(day.year, day.month)[1]))
        part = financial(db, bid, day, last, basis, "profit-loss")
        trends.append(
            {
                "start_date": day,
                "end_date": last,
                "income": part["income"],
                "expense": part["expense"],
                "net_profit": part["net_profit"],
            }
        )
        day = last + timedelta(days=1)
    current = balances(db, bid, end)
    from ledgerpilot.shared.operations import account

    if len(bank_ids) != len(set(bank_ids)):
        raise DomainError("ERR_ACCOUNT", "Cash accounts cannot be selected twice.")
    for aid in bank_ids:
        account(db, bid, aid, {"asset"}, plain=True)
    return {
        **totals,
        "kind": "dashboard",
        "trend": trends,
        "cash_balance": str(sum((current.get(aid, Decimal(0)) for aid in bank_ids), Decimal(0))),
        "cash_account_ids": [str(aid) for aid in bank_ids],
        "receivables": aging(db, bid, end, "invoice"),
        "payables": aging(db, bid, end, "bill"),
        "controls": controls(db, bid, end),
    }
