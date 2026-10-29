"""Correct a reviewed category through an immutable linked GL adjustment."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import JournalLine
from ledgerpilot.banking.models import BankCorrection
from ledgerpilot.banking.schemas import Split
from ledgerpilot.banking.service import bank_row
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.trade.models import Operation


class Reclassify(ops.Command):
    accounting_date: date
    reason: str = Field(min_length=1, max_length=1000)
    splits: list[Split] = Field(min_length=1, max_length=90)


def correct(db: Session, who: Principal, bid: UUID, rid: UUID, data: Reclassify) -> dict[str, Any]:
    business, old = ops.begin(db, who, bid, data, f"bank.{rid}.reclassified")
    if old is not None:
        return old
    row = bank_row(db, bid, rid)
    original = db.scalar(
        select(Operation).where(
            Operation.business_id == bid, Operation.journal_id == row.journal_id
        )
    )
    if (
        row.state != "categorized"
        or not original
        or original.result.get("disposition") not in {"categorize", "split"}
    ):
        raise DomainError(
            "ERR_CORRECTION_SOURCE",
            "Reclassification requires an original category/split; "
            "correct source settlements in their subledger.",
            409,
        )
    previous = list(
        db.scalars(
            select(BankCorrection).where(
                BankCorrection.business_id == bid, BankCorrection.row_id == rid
            )
        )
    )
    if data.accounting_date < max([row.accounting_date, *(p.accounting_date for p in previous)]):
        raise DomainError(
            "ERR_CORRECTION_DATE",
            "A correction cannot predate the source or its latest correction.",
            409,
        )
    journals = [row.journal_id, *(p.journal_id for p in previous)]
    current = dict(
        db.execute(
            select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
            .where(
                JournalLine.business_id == bid,
                JournalLine.journal_id.in_(journals),
                JournalLine.account_id != row.account_id,
            )
            .group_by(JournalLine.account_id)
        ).all()
    )
    desired: dict[UUID, Decimal] = defaultdict(Decimal)
    total = Decimal(0)
    for split in data.splits:
        if split.account_id == row.account_id:
            raise DomainError("ERR_CORRECTION", "Reclassification cannot change the bank movement.")
        ops.account(
            db,
            bid,
            split.account_id,
            {"income", "expense", "asset", "liability", "equity"},
            plain=True,
        )
        amount = ops.money(split.amount, business)
        desired[split.account_id] += amount * (-1 if row.amount > 0 else 1)
        total += amount
    if total != abs(row.amount):
        raise DomainError(
            "ERR_CORRECTION",
            "Approved replacement categories must equal the original bank movement.",
        )
    lines = []
    for aid in sorted(set(current) | set(desired), key=str):
        delta = desired.get(aid, Decimal(0)) - current.get(aid, Decimal(0))
        if delta:
            ops.account(
                db, bid, aid, {"income", "expense", "asset", "liability", "equity"}, plain=True
            )
            lines.append(ops.line(aid, abs(delta), debit=delta > 0))
    if not lines:
        raise DomainError("ERR_NO_CHANGE", "The approved categories are already current.")
    jid = ops.publish(db, who, business, data, data.accounting_date, data.reason, lines)
    item = BankCorrection(
        business_id=bid,
        row_id=rid,
        accounting_date=data.accounting_date,
        journal_id=jid,
        reason=data.reason,
        splits=[s.model_dump(mode="json") for s in data.splits],
        created_by=who.user.id,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        f"bank.{rid}.reclassified",
        {
            "id": str(item.id),
            "journal_id": str(jid),
            "original_row_id": str(rid),
            "bank_movement_changed": False,
        },
        jid,
    )
