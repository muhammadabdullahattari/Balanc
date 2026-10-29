"""Bounded source lookup and set-based outstanding balance queries."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError
from ledgerpilot.trade.models import Allocation, Loan, LoanMovement, Party, Payment, TradeDocument
from ledgerpilot.workspace.models import AdvanceApplication


def party(db: Session, bid: UUID, pid: UUID, role: str | None = None) -> Party:
    item = db.scalar(select(Party).where(Party.business_id == bid, Party.id == pid))
    if not item or not item.active or (role and role not in item.roles):
        raise DomainError("ERR_PARTY", "Party is unavailable or has an incompatible role.")
    return item


def document(db: Session, bid: UUID, did: UUID) -> TradeDocument:
    item = db.scalar(
        select(TradeDocument).where(
            TradeDocument.business_id == bid,
            TradeDocument.id == did,
        )
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Document not found.", 404)
    return item


def due(db: Session, bid: UUID, as_of: date) -> dict[UUID, Decimal]:
    allocated = dict(
        db.execute(
            select(Allocation.document_id, func.sum(Allocation.amount))
            .where(
                Allocation.business_id == bid,
                Allocation.accounting_date <= as_of,
            )
            .group_by(Allocation.document_id)
        ).all()
    )
    credits = dict(
        db.execute(
            select(TradeDocument.original_id, func.sum(TradeDocument.total))
            .where(
                TradeDocument.business_id == bid,
                TradeDocument.original_id.is_not(None),
                TradeDocument.accounting_date <= as_of,
            )
            .group_by(TradeDocument.original_id)
        ).all()
    )
    values = {
        did: total - allocated.get(did, Decimal(0)) - credits.get(did, Decimal(0))
        for did, total in db.execute(
            select(TradeDocument.id, TradeDocument.total).where(
                TradeDocument.business_id == bid,
                TradeDocument.kind.in_(["invoice", "bill"]),
                TradeDocument.accounting_date <= as_of,
            )
        )
    }
    applications = db.execute(
        select(AdvanceApplication.document_id, func.sum(AdvanceApplication.amount))
        .where(AdvanceApplication.business_id == bid, AdvanceApplication.accounting_date <= as_of)
        .group_by(AdvanceApplication.document_id)
    )
    for did, amount in applications:
        if did in values:
            values[did] -= amount
    payments = list(
        db.scalars(
            select(Payment).where(
                Payment.business_id == bid,
                Payment.accounting_date <= as_of,
            )
        )
    )
    for payment in payments:
        if payment.original_payment_id is not None:
            for row in payment.allocations:
                values[UUID(row["document_id"])] += Decimal(row["amount"])
    return values


def loan(db: Session, bid: UUID, lid: UUID) -> Loan:
    item = db.scalar(select(Loan).where(Loan.business_id == bid, Loan.id == lid))
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Loan not found.", 404)
    return item


def principal(db: Session, bid: UUID, lid: UUID, as_of: date) -> Decimal:
    from sqlalchemy import case

    return db.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            LoanMovement.kind.in_(["drawdown", "opening", "opening_increase"]),
                            LoanMovement.principal,
                        ),
                        else_=-LoanMovement.principal,
                    )
                ),
                0,
            )
        ).where(
            LoanMovement.business_id == bid,
            LoanMovement.loan_id == lid,
            LoanMovement.accounting_date <= as_of,
        )
    ) or Decimal(0)


def principal_balances(
    db: Session, bid: UUID, lids: list[UUID], as_of: date
) -> dict[UUID, Decimal]:
    from sqlalchemy import case

    return dict(
        db.execute(
            select(
                LoanMovement.loan_id,
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
            .where(
                LoanMovement.business_id == bid,
                LoanMovement.loan_id.in_(lids),
                LoanMovement.accounting_date <= as_of,
            )
            .group_by(LoanMovement.loan_id)
        ).all()
    )
