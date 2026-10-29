"""Explicit deposit accounts keep unapplied advances separate from trade AR/AP."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.schemas import exact_amount
from ledgerpilot.workspace.models import Advance, AdvanceApplication


class AdvanceCreate(ops.Command):
    party_id: UUID
    direction: Literal["customer", "vendor"]
    accounting_date: date
    deposit_account_id: UUID
    bank_account_id: UUID
    amount: str
    _money = field_validator("amount")(exact_amount)


class AdvanceApply(ops.Command):
    document_id: UUID
    accounting_date: date
    amount: str
    _money = field_validator("amount")(exact_amount)


def create(db: Session, who: Principal, bid: UUID, data: AdvanceCreate) -> dict[str, Any]:
    business, old = ops.begin(db, who, bid, data, "advance.received")
    if old is not None:
        return old
    customer = data.direction == "customer"
    repo.party(db, bid, data.party_id, "customer" if customer else "supplier")
    ops.account(db, bid, data.bank_account_id, {"asset"}, plain=True)
    ops.account(
        db, bid, data.deposit_account_id, {"liability"} if customer else {"asset"}, plain=True
    )
    if data.bank_account_id == data.deposit_account_id:
        raise DomainError("ERR_ADVANCE", "Deposit and bank accounts must differ.")
    amount = ops.money(data.amount, business)
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.accounting_date,
        "Unapplied advance",
        [
            ops.line(data.bank_account_id, amount, debit=customer),
            ops.line(data.deposit_account_id, amount, debit=not customer),
        ],
    )
    item = Advance(
        business_id=bid,
        party_id=data.party_id,
        direction=data.direction,
        accounting_date=data.accounting_date,
        deposit_account_id=data.deposit_account_id,
        bank_account_id=data.bank_account_id,
        amount=amount,
        journal_id=jid,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db, who, bid, data, "advance.received", {"id": str(item.id), "journal_id": str(jid)}, jid
    )


def apply(db: Session, who: Principal, bid: UUID, aid: UUID, data: AdvanceApply) -> dict[str, Any]:
    business, old = ops.begin(db, who, bid, data, f"advance.{aid}.applied")
    if old is not None:
        return old
    advance = db.scalar(select(Advance).where(Advance.business_id == bid, Advance.id == aid))
    if not advance:
        raise DomainError("ERR_NOT_FOUND", "Advance not found.", 404)
    doc = repo.document(db, bid, data.document_id)
    customer = advance.direction == "customer"
    amount = ops.money(data.amount, business)
    used = db.scalar(
        select(func.coalesce(func.sum(AdvanceApplication.amount), 0)).where(
            AdvanceApplication.business_id == bid,
            AdvanceApplication.advance_id == aid,
        )
    ) or Decimal(0)
    if (
        doc.kind != ("invoice" if customer else "bill")
        or doc.party_id != advance.party_id
        or data.accounting_date < max(doc.accounting_date, advance.accounting_date)
        or used + amount > advance.amount
        or amount > repo.due(db, bid, data.accounting_date).get(doc.id, Decimal(0))
        or amount > repo.due(db, bid, date.max).get(doc.id, Decimal(0))
    ):
        raise DomainError(
            "ERR_ADVANCE", "Advance allocation exceeds compatible available funds.", 409
        )
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.accounting_date,
        "Apply advance",
        [
            ops.line(advance.deposit_account_id, amount, debit=customer),
            ops.line(doc.control_account_id, amount, debit=not customer),
        ],
    )
    item = AdvanceApplication(
        business_id=bid,
        advance_id=aid,
        document_id=doc.id,
        accounting_date=data.accounting_date,
        amount=amount,
        journal_id=jid,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        f"advance.{aid}.applied",
        {"id": str(item.id), "journal_id": str(jid)},
        jid,
    )
