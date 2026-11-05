"""Bounded source lookup and set-based outstanding balance queries."""

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.query_limits import REPORT_LIMIT
from ledgerpilot.trade.models import Loan, LoanMovement, Party, TradeDocument


def party(db: Session, bid: UUID, pid: UUID, role: str | None = None) -> Party:
    item = db.scalar(select(Party).where(Party.business_id == bid, Party.id == pid))
    return validate_party(item, role)


def validate_party(item: Party | None, role: str | None = None) -> Party:
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


def documents(db: Session, bid: UUID, ids: list[UUID]) -> dict[UUID, TradeDocument]:
    if not ids:
        return {}
    return {
        item.id: item
        for item in db.scalars(
            select(TradeDocument).where(
                TradeDocument.business_id == bid, TradeDocument.id.in_(set(ids))
            )
        )
    }


def require_document(items: dict[UUID, TradeDocument], did: UUID) -> TradeDocument:
    item = items.get(did)
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Document not found.", 404)
    return item


def due(db: Session, bid: UUID, as_of: date) -> dict[UUID, Decimal]:
    return due_snapshots(db, bid, [as_of])[as_of]


def due_snapshots(
    db: Session, bid: UUID, dates: list[date], document_ids: list[UUID] | None = None
) -> dict[date, dict[UUID, Decimal]]:
    """One exact, tenant-scoped SQL aggregate, including credited cash refunds.

    Keep historical and current balances separate: later payments must not alter
    the earlier snapshot used by reminders or backdated allocation validation.
    """
    dates = sorted(set(dates))
    if not dates or len(dates) > 2:
        raise ValueError("Outstanding snapshots require one or two dates")
    values: dict[date, dict[UUID, Decimal]] = {day: {} for day in dates}
    if document_ids == []:
        return values
    rows = db.execute(
        text("""
    WITH cutoffs AS (SELECT unnest(CAST(:dates AS date[])) AS day),
    docs AS (SELECT id,total,accounting_date FROM app.trade_documents
      WHERE business_id=:b AND kind IN ('invoice','bill') AND accounting_date<=:end
        AND (:all_ids OR id=ANY(CAST(:ids AS uuid[])))),
    allocations AS (SELECT c.day,a.document_id AS id,sum(a.amount) AS amount
      FROM cutoffs c JOIN app.allocations a ON a.business_id=:b AND a.accounting_date<=c.day
      JOIN docs d ON d.id=a.document_id GROUP BY c.day,a.document_id),
    credits AS (SELECT c.day,t.original_id AS id,sum(t.total) AS amount
      FROM cutoffs c JOIN app.trade_documents t ON t.business_id=:b AND t.accounting_date<=c.day
      JOIN docs d ON d.id=t.original_id GROUP BY c.day,t.original_id),
    advances AS (SELECT c.day,a.document_id AS id,sum(a.amount) AS amount
      FROM cutoffs c JOIN app.advance_applications a
        ON a.business_id=:b AND a.accounting_date<=c.day
      JOIN docs d ON d.id=a.document_id GROUP BY c.day,a.document_id),
    refunds AS (SELECT c.day,(a->>'document_id')::uuid AS id,sum((a->>'amount')::numeric) AS amount
      FROM cutoffs c JOIN app.payments p ON p.business_id=:b AND p.accounting_date<=c.day
        AND p.original_payment_id IS NOT NULL
      CROSS JOIN LATERAL jsonb_array_elements(p.allocations) a
      JOIN docs d ON d.id=(a->>'document_id')::uuid GROUP BY c.day,(a->>'document_id')::uuid)
    SELECT c.day,d.id,d.total-coalesce(a.amount,0)-coalesce(t.amount,0)
      -coalesce(v.amount,0)+coalesce(r.amount,0)
    FROM cutoffs c JOIN docs d ON d.accounting_date<=c.day
    LEFT JOIN allocations a ON a.day=c.day AND a.id=d.id
    LEFT JOIN credits t ON t.day=c.day AND t.id=d.id
    LEFT JOIN advances v ON v.day=c.day AND v.id=d.id
    LEFT JOIN refunds r ON r.day=c.day AND r.id=d.id
    ORDER BY c.day,d.id LIMIT :cap
    """),
        {
            "b": bid,
            "dates": dates,
            "end": dates[-1],
            "all_ids": document_ids is None,
            "ids": document_ids or [],
            "cap": REPORT_LIMIT * len(dates) + 1,
        },
    )
    for row in rows:
        day, did, amount = cast(date, row[0]), cast(UUID, row[1]), cast(Decimal, row[2])
        values[day][did] = amount
        if len(values[day]) > REPORT_LIMIT:
            raise DomainError("ERR_QUOTA", "Narrow the pilot outstanding-balance scope.", 413)
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
