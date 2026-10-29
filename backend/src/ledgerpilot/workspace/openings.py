"""Accountant-approved cutover balances, distinct from historic sales and cash."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy.orm import Session

from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize
from ledgerpilot.trade.models import Loan, LoanMovement, TradeDocument
from ledgerpilot.trade.repository import party
from ledgerpilot.workspace.models import OpeningItem


class OpeningRow(Schema):
    kind: Literal["customer", "supplier", "loan_payable", "loan_receivable"]
    party_id: UUID
    reference: str = Field(min_length=1, max_length=80, pattern=r"^[^<>\x00-\x1f]+$")
    original_date: date
    due_date: date
    control_account_id: UUID
    amount: str

    @field_validator("amount")
    @classmethod
    def exact_amount(cls, value: str) -> str:
        return LineCreate.money(value)

    @model_validator(mode="after")
    def original_due_date(self) -> "OpeningRow":
        if self.due_date < self.original_date:
            raise ValueError("Due date precedes original source date")
        return self


class OpeningCommand(OpeningRow, ops.Command):
    cutover_date: date
    equity_account_id: UUID
    review_reason: str = Field(min_length=1, max_length=1000)


def validate(db: Session, bid: UUID, row: OpeningRow, cutover: date, equity: UUID) -> None:
    from ledgerpilot.shared.errors import DomainError
    from ledgerpilot.tenancy.service import get_business

    if row.original_date > cutover:
        raise DomainError("ERR_OPENING_DATE", "Original source date must be on/before cutover.")
    role, kind, control = {
        "customer": ("customer", "asset", "ar"),
        "supplier": ("supplier", "liability", "ap"),
        "loan_payable": ("lender", "liability", "loan_payable"),
        "loan_receivable": ("borrower", "asset", "loan_receivable"),
    }[row.kind]
    party(db, bid, row.party_id, role)
    ops.account(db, bid, row.control_account_id, {kind}, control)
    ops.account(db, bid, equity, {"equity"}, plain=True)
    ops.money(row.amount, get_business(db, bid))


def create(db: Session, who: Principal, bid: UUID, data: OpeningCommand) -> dict[str, Any]:
    authorize(db, who, bid, {"accountant"}, posting=True)
    business, old = ops.begin(db, who, bid, data, "migration.opening")
    if old is not None:
        return old
    validate(db, bid, data, data.cutover_date, data.equity_account_id)
    value = Decimal(data.amount)
    debit = data.kind in {"customer", "loan_receivable"}
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.cutover_date,
        "Reviewed opening: " + data.reference,
        [
            ops.line(data.control_account_id, value, debit),
            ops.line(data.equity_account_id, value, not debit),
        ],
    )
    doc_id, movement_id = None, None
    if data.kind in {"customer", "supplier"}:
        document = TradeDocument(
            business_id=bid,
            kind="invoice" if data.kind == "customer" else "bill",
            number=data.reference,
            party_id=data.party_id,
            accounting_date=data.cutover_date,
            opening_source_date=data.original_date,
            due_date=data.due_date,
            control_account_id=data.control_account_id,
            journal_id=jid,
            total=value,
            tax=Decimal(0),
            lines=[
                {
                    "account_id": str(data.equity_account_id),
                    "description": "Opening balance",
                    "amount": data.amount,
                    "tax_amount": "0",
                    "tax_account_id": None,
                    "tax_code": None,
                }
            ],
        )
        db.add(document)
        db.flush()
        doc_id = document.id
    else:
        loan = Loan(
            business_id=bid,
            party_id=data.party_id,
            direction="payable" if data.kind == "loan_payable" else "receivable",
            control_account_id=data.control_account_id,
            name=data.reference,
            schedule=[],
        )
        db.add(loan)
        db.flush()
        movement = LoanMovement(
            business_id=bid,
            loan_id=loan.id,
            accounting_date=data.cutover_date,
            kind="opening",
            principal=value,
            interest=Decimal(0),
            fees=Decimal(0),
            journal_id=jid,
        )
        db.add(movement)
        db.flush()
        movement_id = movement.id
    item = OpeningItem(
        business_id=bid,
        kind=data.kind,
        party_id=data.party_id,
        reference=data.reference,
        original_date=data.original_date,
        due_date=data.due_date,
        cutover_date=data.cutover_date,
        amount=value,
        equity_account_id=data.equity_account_id,
        document_id=doc_id,
        loan_movement_id=movement_id,
        reviewed_by=who.user.id,
        review_reason=data.review_reason,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        "migration.opening",
        {
            "id": str(doc_id or movement_id),
            "opening_item_id": str(item.id),
            "journal_id": str(jid),
            "kind": data.kind,
            "document_id": str(doc_id) if doc_id else None,
            "loan_movement_id": str(movement_id) if movement_id else None,
            "amount": str(value),
            "reviewed_by": str(who.user.id),
        },
        jid,
    )
