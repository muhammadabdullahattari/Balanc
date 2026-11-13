"""Evaluate a single saved rule against reviewed entries without posting."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from ledgerpilot.accounting.models import Account, Journal, JournalLine
from ledgerpilot.banking.models import BankCorrection, BankRow, BankRule
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import get_business


def evaluate(
    db: Session, bid: UUID, rule: BankRule, start: date, end: date, limit_amount: Decimal
) -> dict[str, Any]:
    if not 0 <= (end - start).days < 93:
        raise DomainError("ERR_DATE_RANGE", "Choose a date range of at most 93 days.", 422)
    limit_amount = ops.money(format(limit_amount, "f"), get_business(db, bid))
    target = ops.account(db, bid, rule.account_id, {"income", "expense"}, plain=True)
    other = aliased(JournalLine)
    reversal = aliased(Journal)
    rows = list(
        db.execute(
            select(BankRow.id, JournalLine.account_id)
            .join(
                Journal,
                (Journal.business_id == BankRow.business_id) & (Journal.id == BankRow.journal_id),
            )
            .join(
                JournalLine,
                (JournalLine.business_id == Journal.business_id)
                & (JournalLine.journal_id == Journal.id),
            )
            .join(
                Account,
                (Account.business_id == JournalLine.business_id)
                & (Account.id == JournalLine.account_id),
            )
            .where(
                BankRow.business_id == bid,
                BankRow.state == "categorized",
                BankRow.accounting_date.between(start, end),
                BankRow.description.icontains(rule.contains, autoescape=True),
                BankRow.amount.between(Decimal(0), limit_amount)
                if target.kind == "income"
                else BankRow.amount.between(-limit_amount, Decimal(0)),
                BankRow.amount != 0,
                Journal.status == "posted",
                JournalLine.account_id != BankRow.account_id,
                JournalLine.debit - JournalLine.credit == -BankRow.amount,
                Account.kind.in_(("income", "expense")),
                Account.control.is_(None),
                ~select(other.position)
                .where(
                    other.business_id == bid,
                    other.journal_id == Journal.id,
                    other.account_id != BankRow.account_id,
                    other.position != JournalLine.position,
                )
                .exists(),
                ~select(reversal.id)
                .where(
                    reversal.business_id == bid,
                    reversal.reversal_of == Journal.id,
                    reversal.status == "posted",
                )
                .exists(),
                ~select(BankCorrection.id)
                .where(
                    BankCorrection.business_id == bid,
                    BankCorrection.row_id == BankRow.id,
                )
                .exists(),
            )
            .order_by(BankRow.id)
            .limit(501)
        )
    )
    if len(rows) > 500:
        raise DomainError("ERR_QUOTA", "Narrow the date range or amount cap to 500 rows.", 413)
    mismatches = [row_id for row_id, actual in rows if actual != rule.account_id]
    return {
        "rule_id": rule.id,
        "rule_version": rule.version,
        "evaluated": len(rows),
        "matched": len(rows) - len(mismatches),
        "mismatched_row_ids": mismatches,
    }
