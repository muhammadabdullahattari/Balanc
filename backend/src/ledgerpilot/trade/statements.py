"""Role-separated party activity with server-calculated running balances."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.reports.service import context
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.trade.models import Party
from ledgerpilot.workspace.service import save_report


def statement(
    db: Session,
    who: Principal,
    bid: UUID,
    pid: UUID,
    as_of: date,
    role: str,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    party = db.scalar(select(Party).where(Party.business_id == bid, Party.id == pid))
    if not party or role not in party.roles:
        raise DomainError("ERR_NOT_FOUND", "Party role not found.", 404)
    after_date, after_kind, after_id = None, "", None
    if cursor:
        try:
            day, after_kind, key = cursor.split("|", 2)
            after_date, after_id = date.fromisoformat(day), UUID(key)
        except ValueError:
            raise DomainError("ERR_CURSOR", "Invalid statement cursor.", 422) from None
    params: dict[str, Any] = {
        "bid": bid,
        "party": pid,
        "asof": as_of,
        "after_date": after_date,
        "after_kind": after_kind,
        "after_id": after_id,
        "limit": limit + 1,
    }
    if role in {"customer", "supplier"}:
        sale = role == "customer"
        params.update(
            source_kind="invoice" if sale else "bill",
            credit_kind="customer_credit" if sale else "vendor_credit",
            immediate="sales_receipt" if sale else "__none__",
            payment_kind="receipt" if sale else "payment",
            refund_kind="customer_refund" if sale else "vendor_refund",
            direction="customer" if sale else "vendor",
        )
        events = """
          SELECT d.id,d.accounting_date,d.kind,d.number,d.journal_id,d.total AS amount,
           CASE WHEN d.kind=:source_kind THEN d.total WHEN d.kind=:credit_kind THEN -d.total
           ELSE 0::numeric END AS effect,0::numeric AS interest,0::numeric AS fees
          FROM app.trade_documents d WHERE d.business_id=:bid AND d.party_id=:party
           AND d.kind IN (:source_kind,:credit_kind,:immediate)
          UNION ALL
          SELECT p.id,p.accounting_date,p.kind,'' AS number,p.journal_id,p.total,
           CASE WHEN p.kind=:payment_kind THEN -p.total ELSE p.total END,0::numeric,p.fee_amount
          FROM app.payments p WHERE p.business_id=:bid AND p.party_id=:party
           AND p.kind IN (:payment_kind,:refund_kind)
          UNION ALL
          SELECT a.id,a.accounting_date,'advance','',a.journal_id,a.amount,
           0::numeric,0::numeric,0::numeric
          FROM app.advances a
          WHERE a.business_id=:bid AND a.party_id=:party AND a.direction=:direction
          UNION ALL
          SELECT x.id,x.accounting_date,'advance_applied','',x.journal_id,x.amount,
           -x.amount,0::numeric,0::numeric
          FROM app.advance_applications x JOIN app.advances a
           ON a.business_id=x.business_id AND a.id=x.advance_id
          WHERE x.business_id=:bid AND a.party_id=:party AND a.direction=:direction
        """
    else:
        params["direction"] = "payable" if role == "lender" else "receivable"
        events = """
          SELECT m.id,m.accounting_date,m.kind,l.name AS number,m.journal_id,m.principal AS amount,
           CASE WHEN m.kind IN ('drawdown','opening','opening_increase') THEN m.principal
            ELSE -m.principal END AS effect,
           m.interest,m.fees
          FROM app.loan_movements m JOIN app.loans l
           ON l.business_id=m.business_id AND l.id=m.loan_id
          WHERE m.business_id=:bid AND l.party_id=:party AND l.direction=:direction
        """
    cte = (
        "WITH events AS ("
        + events
        + "),scoped AS (SELECT * FROM events WHERE accounting_date<=:asof) "
    )
    total = db.scalar(text(cte + "SELECT coalesce(sum(effect),0) FROM scoped"), params)
    query = (
        cte
        + """, ranked AS (
      SELECT *,sum(effect) OVER(ORDER BY accounting_date,kind,id ROWS UNBOUNDED PRECEDING)
       AS running_balance FROM scoped
    ) SELECT * FROM ranked WHERE CAST(:after_date AS date) IS NULL OR
     (accounting_date,kind,id)>(CAST(:after_date AS date),:after_kind,CAST(:after_id AS uuid))
    ORDER BY accounting_date,kind,id LIMIT :limit
    """
    )
    rows = list(db.execute(text(query), params).mappings())
    items = [
        {key: str(value) if isinstance(value, Decimal) else value for key, value in r.items()}
        for r in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = f"{last['accounting_date']}|{last['kind']}|{last['id']}"
    result = {
        **context(db, bid, as_of, "operational_party_ledger"),
        "kind": "party-statement",
        "party_id": str(pid),
        "party_name": party.name,
        "role": role,
        "items": items,
        "next_cursor": next_cursor,
        "total": str(total),
        "balance_definition": (
            "Trade amount due for customer/supplier; principal only for lender/borrower. "
            "Advances remain separate; roles are never netted."
        ),
    }
    return save_report(db, who, bid, result)
