"""Statement reconciliation uses individually identified, unconsumed ledger items."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Journal, JournalLine
from ledgerpilot.banking.models import BankMatch, BankRow, Statement
from ledgerpilot.banking.schemas import StatementCreate
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.reports.service import balances
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.service import evidence


def sign_off(
    db: Session,
    who: Principal,
    bid: UUID,
    data: StatementCreate,
) -> dict[str, Any]:
    business, old = ops.begin(db, who, bid, data, "statement.signed")
    if old is not None:
        return old
    ops.account(db, bid, data.account_id, {"asset", "liability"}, plain=True)
    evidence(db, bid, data.evidence_id)
    closing = Decimal(data.closing_balance)
    ops.money(format(abs(closing), "f"), business, positive=False)
    if len({(r.journal_id, r.position) for r in data.outstanding}) != len(data.outstanding):
        raise DomainError("ERR_RECONCILIATION", "An outstanding item cannot be selected twice.")
    outstanding = Decimal(0)
    for item in data.outstanding:
        value = db.scalar(
            select(JournalLine.debit - JournalLine.credit)
            .join(
                Journal,
                (Journal.id == JournalLine.journal_id)
                & (Journal.business_id == JournalLine.business_id),
            )
            .where(
                JournalLine.business_id == bid,
                JournalLine.account_id == data.account_id,
                JournalLine.journal_id == item.journal_id,
                JournalLine.position == item.position,
                Journal.status == "posted",
                Journal.accounting_date <= data.as_of,
            )
        )
        matched = db.scalar(
            select(BankMatch.id)
            .join(
                BankRow,
                (BankMatch.row_id == BankRow.id) & (BankMatch.business_id == BankRow.business_id),
            )
            .where(
                BankRow.business_id == bid,
                BankRow.account_id == data.account_id,
                BankMatch.journal_id == item.journal_id,
                BankRow.accounting_date <= data.as_of,
            )
        )
        if value is None or matched:
            raise DomainError(
                "ERR_RECONCILIATION", "Outstanding item is unavailable or already bank-matched."
            )
        outstanding += value
    pending = (
        db.scalar(
            select(func.count())
            .select_from(BankRow)
            .where(
                BankRow.business_id == bid,
                BankRow.account_id == data.account_id,
                BankRow.accounting_date <= data.as_of,
                BankRow.state == "for_review",
            )
        )
        or 0
    )
    ledger = balances(db, bid, data.as_of).get(data.account_id, Decimal(0))
    difference = ledger - closing - outstanding
    if difference or pending:
        raise DomainError(
            "ERR_RECONCILIATION",
            "Resolve unexplained differences and bank-review items first.",
            409,
        )
    statement = Statement(
        business_id=bid,
        account_id=data.account_id,
        as_of=data.as_of,
        closing_balance=closing,
        outstanding=[r.model_dump(mode="json") for r in data.outstanding],
        ledger_balance=ledger,
        evidence_id=data.evidence_id,
        reviewer_id=who.user.id,
    )
    db.add(statement)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        "statement.signed",
        {
            "id": str(statement.id),
            "ledger_balance": str(ledger),
            "statement_balance": str(closing),
            "outstanding": str(outstanding),
            "unexplained_difference": "0",
        },
    )
