"""Bounded ledger queries; scope is also enforced by PostgreSQL RLS."""

from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account, Journal, JournalLine


def journal(db: Session, business_id: UUID, journal_id: UUID) -> Journal | None:
    return db.scalar(
        select(Journal).where(Journal.business_id == business_id, Journal.id == journal_id)
    )


def lines(db: Session, business_id: UUID, journal_id: UUID) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine)
            .where(JournalLine.business_id == business_id, JournalLine.journal_id == journal_id)
            .order_by(JournalLine.position)
        )
    )


def trial_balance(db: Session, business_id: UUID, as_of: date) -> list[dict[str, object]]:
    rows = db.execute(
        text("""
        SELECT a.id,a.code,a.name,a.kind,
        coalesce(sum(l.debit) FILTER(WHERE j.accounting_date<=:date),0) AS debit,
        coalesce(sum(l.credit) FILTER(WHERE j.accounting_date<=:date),0) AS credit
        FROM app.accounts a LEFT JOIN app.journal_lines l
        ON l.business_id=a.business_id AND l.account_id=a.id
        LEFT JOIN app.journals j ON j.business_id=l.business_id AND j.id=l.journal_id
         AND j.status='posted'
        WHERE a.business_id=:b GROUP BY a.id,a.code,a.name,a.kind ORDER BY a.code
    """),
        {"b": business_id, "date": as_of},
    ).mappings()
    result: list[dict[str, object]] = []
    with localcontext() as context:
        context.prec = 60
        for r in rows:
            balance = r["debit"] - r["credit"]
            result.append(
                {
                    "account_id": str(r["id"]),
                    "code": r["code"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "debit": format(max(balance, Decimal(0)), ".6f"),
                    "credit": format(max(-balance, Decimal(0)), ".6f"),
                    "balance": format(balance, ".6f"),
                    "movement_debit": format(r["debit"], ".6f"),
                    "movement_credit": format(r["credit"], ".6f"),
                }
            )
    return result


def account(db: Session, business_id: UUID, account_id: UUID) -> Account | None:
    return db.scalar(
        select(Account).where(Account.business_id == business_id, Account.id == account_id)
    )


def reversal_id(db: Session, business_id: UUID, journal_id: UUID) -> UUID | None:
    return db.scalar(
        select(Journal.id).where(
            Journal.business_id == business_id,
            Journal.reversal_of == journal_id,
            Journal.status == "posted",
        )
    )
