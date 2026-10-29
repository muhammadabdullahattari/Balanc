"""Linked source corrections and evidence-backed cash treatment for opening balances."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Journal
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.models import LoanMovement, TradeDocument
from ledgerpilot.trade.schemas import exact_amount
from ledgerpilot.workspace.models import OpeningCashPolicy, OpeningCorrection, OpeningItem
from ledgerpilot.workspace.service import evidence


class Correction(ops.Command):
    accounting_date: date
    direction: Literal["increase", "decrease"]
    amount: str
    reason: str = Field(min_length=1, max_length=1000)
    source_document_id: UUID | None = None
    _money = field_validator("amount")(exact_amount)


class CashComponent(Schema):
    account_id: UUID
    weight: str
    _money = field_validator("weight")(exact_amount)


class ScopedCorrection(Correction):
    opening_id: UUID


class CashTreatment(Schema):
    treatment: Literal["on_settlement", "previously_recognized"]
    tax_treatment: Literal["no_tax_component", "separately_accounted"]
    components: list[CashComponent] = Field(default_factory=list, max_length=20)
    evidence_id: UUID
    reason: str = Field(min_length=1, max_length=1000)


def source(db: Session, bid: UUID, oid: UUID) -> OpeningItem:
    item = db.scalar(
        select(OpeningItem).where(OpeningItem.business_id == bid, OpeningItem.id == oid)
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Opening balance not found.", 404)
    return item


def correct(db: Session, who: Principal, bid: UUID, oid: UUID, data: Correction) -> dict[str, Any]:
    authorize(db, who, bid, {"accountant"}, posting=True)
    data = ScopedCorrection.model_validate({**data.model_dump(), "opening_id": oid})
    business, old = ops.begin(db, who, bid, data, "opening.corrected")
    if old is not None:
        return old
    original = source(db, bid, oid)
    latest = db.scalar(
        select(func.max(Journal.accounting_date)).where(
            Journal.business_id == bid, Journal.status == "posted"
        )
    )
    if data.accounting_date < max(original.cutover_date, latest or original.cutover_date):
        raise DomainError(
            "ERR_CORRECTION_DATE", "Opening corrections cannot predate posted activity.", 409
        )
    amount = ops.money(data.amount, business)
    increase = data.direction == "increase"
    trade = original.document_id is not None
    sale = original.kind in {"customer", "loan_receivable"}
    if trade:
        assert original.document_id is not None
        target = data.source_document_id or original.document_id
        if target != original.document_id and not db.scalar(
            select(OpeningCorrection.id).where(
                OpeningCorrection.business_id == bid,
                OpeningCorrection.opening_id == oid,
                OpeningCorrection.document_id == target,
                OpeningCorrection.delta > 0,
            )
        ):
            raise DomainError(
                "ERR_CORRECTION_SOURCE", "Select a source in this opening history.", 422
            )
        doc = repo.document(db, bid, target)
        control = doc.control_account_id
        previous_credits = db.scalar(
            select(func.coalesce(func.sum(TradeDocument.total), 0)).where(
                TradeDocument.business_id == bid, TradeDocument.original_id == doc.id
            )
        ) or Decimal(0)
        if not increase and amount > doc.total - previous_credits:
            raise DomainError(
                "ERR_OPENING_BALANCE", "Decrease exceeds the uncorrected original balance.", 409
            )
    else:
        if data.source_document_id is not None:
            raise DomainError(
                "ERR_CORRECTION_SOURCE", "Loan corrections do not select a trade source.", 422
            )
        movement = db.scalar(
            select(LoanMovement).where(
                LoanMovement.business_id == bid, LoanMovement.id == original.loan_movement_id
            )
        )
        if movement is None:
            raise DomainError("ERR_NOT_FOUND", "Opening principal not found.", 404)
        loan = repo.loan(db, bid, movement.loan_id)
        control = loan.control_account_id
        if not increase and amount > repo.principal(db, bid, loan.id, date.max):
            raise DomainError("ERR_OPENING_BALANCE", "Decrease exceeds remaining principal.", 409)
    ops.account(db, bid, original.equity_account_id, {"equity"}, plain=True)
    debit = sale == increase
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.accounting_date,
        data.reason,
        [ops.line(control, amount, debit), ops.line(original.equity_account_id, amount, not debit)],
    )
    document_id, loan_movement_id = None, None
    if trade:
        new = TradeDocument(
            business_id=bid,
            kind=("invoice" if sale else "bill")
            if increase
            else ("customer_credit" if sale else "vendor_credit"),
            number="OPN-" + uuid4().hex,
            party_id=original.party_id,
            accounting_date=data.accounting_date,
            due_date=original.due_date if increase else data.accounting_date,
            opening_source_date=original.original_date if increase else None,
            control_account_id=control,
            journal_id=jid,
            total=amount,
            tax=Decimal(0),
            original_id=None if increase else doc.id,
            lines=[
                {
                    "account_id": str(original.equity_account_id),
                    "amount": str(amount),
                    "description": "Reviewed opening correction",
                    "tax_amount": "0",
                    "tax_account_id": None,
                    "tax_code": None,
                }
            ],
        )
        db.add(new)
        db.flush()
        document_id = new.id
    else:
        new_movement = LoanMovement(
            business_id=bid,
            loan_id=loan.id,
            accounting_date=data.accounting_date,
            kind="opening_increase" if increase else "opening_decrease",
            principal=amount,
            interest=Decimal(0),
            fees=Decimal(0),
            journal_id=jid,
        )
        db.add(new_movement)
        db.flush()
        loan_movement_id = new_movement.id
    item = OpeningCorrection(
        business_id=bid,
        opening_id=oid,
        accounting_date=data.accounting_date,
        delta=amount if increase else -amount,
        document_id=document_id,
        loan_movement_id=loan_movement_id,
        journal_id=jid,
        reason=data.reason,
        reviewed_by=who.user.id,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        "opening.corrected",
        {
            "id": str(document_id or loan_movement_id),
            "correction_id": str(item.id),
            "journal_id": str(jid),
            "opening_id": str(oid),
            "delta": str(item.delta),
        },
        jid,
    )


def cash_policy(
    db: Session, who: Principal, bid: UUID, oid: UUID, data: CashTreatment
) -> dict[str, Any]:
    authorize(db, who, bid, {"accountant"})
    business = get_business(db, bid, lock=True)
    original = source(db, bid, oid)
    if original.document_id is None:
        raise DomainError(
            "ERR_CASH_POLICY", "Cash trade recognition applies only to AR/AP openings.", 422
        )
    evidence(db, bid, data.evidence_id)
    existing = db.scalar(
        select(OpeningCashPolicy).where(
            OpeningCashPolicy.business_id == bid, OpeningCashPolicy.opening_id == oid
        )
    )
    if existing:
        if (
            existing.treatment,
            existing.components,
            existing.evidence_id,
            existing.reason,
            existing.tax_treatment,
        ) == (
            data.treatment,
            [c.model_dump(mode="json") for c in data.components],
            data.evidence_id,
            data.reason,
            data.tax_treatment,
        ):
            return {"id": str(existing.id), "opening_id": str(oid), "treatment": existing.treatment}
        raise DomainError("ERR_CASH_POLICY", "An approved cash policy is immutable.", 409)
    if data.treatment == "on_settlement":
        if not data.components or len({c.account_id for c in data.components}) != len(
            data.components
        ):
            raise DomainError(
                "ERR_CASH_POLICY", "Select distinct recognition accounts and exact weights.", 422
            )
        for component in data.components:
            ops.account(
                db,
                bid,
                component.account_id,
                {"income"} if original.kind == "customer" else {"expense"},
                plain=True,
            )
            ops.money(component.weight, business)
        if sum((Decimal(c.weight) for c in data.components), Decimal(0)) != original.amount:
            raise DomainError(
                "ERR_CASH_POLICY",
                "Recognition weights must total the original opening amount.",
                422,
            )
    elif data.components:
        raise DomainError(
            "ERR_CASH_POLICY", "Previously recognized balances have no new profit allocation.", 422
        )
    item = OpeningCashPolicy(
        business_id=bid,
        opening_id=oid,
        treatment=data.treatment,
        tax_treatment=data.tax_treatment,
        components=[c.model_dump(mode="json") for c in data.components],
        reason=data.reason,
        evidence_id=data.evidence_id,
        reviewed_by=who.user.id,
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "opening.cash_policy_approved", bid, item.id)
    return {"id": str(item.id), "opening_id": str(oid), "treatment": item.treatment}
