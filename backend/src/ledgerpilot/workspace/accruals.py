"""Autonomous month-end accrual detection and Day-1 auto-reversals (Phase K1 / Module 11)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.auto_post import should_auto_post


def next_month_first(period_end: date) -> date:
    """Return the 1st day of the month immediately following period_end."""
    if period_end.month == 12:
        return date(period_end.year + 1, 1, 1)
    return date(period_end.year, period_end.month + 1, 1)


def build_accrual_journal_lines(
    *,
    amount: Decimal,
    expense_label: str = "Accrued Operating Expense",
    liability_label: str = "Accrued Liabilities",
    reverse: bool = False,
) -> list[dict[str, str]]:
    """Build balanced double-entry lines for an accrual or its Day-1 reversal."""
    amt = abs(amount).quantize(Decimal("0.01"))
    if amt <= Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Accrual amount must be positive.", 422)
    if not reverse:
        return [
            {"account": expense_label, "debit": str(amt), "credit": "0.00"},
            {"account": liability_label, "debit": "0.00", "credit": str(amt)},
        ]
    return [
        {"account": liability_label, "debit": str(amt), "credit": "0.00"},
        {"account": expense_label, "debit": "0.00", "credit": str(amt)},
    ]


def scan_month_end_accruals(
    db: Session,
    business_id: UUID,
    period_end: date,
) -> list[dict[str, Any]]:
    """Scan unbilled purchase orders & active contracts to stage month-end accrual candidates."""
    reverses_on = next_month_first(period_end)
    staged: list[dict[str, Any]] = []

    # 1. Open / partially-received POs with remaining unbilled amount
    po_rows = (
        db.execute(
            text(
                """
                SELECT id, po_number, vendor_name, total_amount, currency
                FROM app.purchase_orders
                WHERE business_id = :bid
                  AND status IN ('open', 'approved', 'partially_received', 'received')
                  AND issue_date <= :period_end
                ORDER BY issue_date ASC
                LIMIT 100
                """
            ),
            {"bid": business_id, "period_end": period_end},
        )
        .mappings()
        .all()
    )
    for po in po_rows:
        amt = Decimal(str(po["total_amount"])).quantize(Decimal("0.01"))
        if amt <= Decimal("0.00"):
            continue
        cid = uuid4()
        desc = f"Unbilled PO {po['po_number']} ({po['vendor_name']})"
        existing = db.execute(
            text(
                """
                SELECT id FROM app.accrual_candidates
                WHERE business_id = :bid AND period_end = :pe AND source_id = :sid
                LIMIT 1
                """
            ),
            {"bid": business_id, "pe": period_end, "sid": po["id"]},
        ).scalar_one_or_none()
        if existing:
            continue
        db.execute(
            text(
                """
                INSERT INTO app.accrual_candidates
                    (id, business_id, period_end, source_kind, source_id, description,
                     amount, currency, reverses_on, status, metadata)
                VALUES
                    (:id, :bid, :pe, 'unbilled_po', :sid, :desc,
                     :amt, :ccy, :rev, 'pending', CAST(:meta AS jsonb))
                """
            ),
            {
                "id": cid,
                "bid": business_id,
                "pe": period_end,
                "sid": po["id"],
                "desc": desc[:250],
                "amt": amt,
                "ccy": str(po["currency"] or "USD")[:3],
                "rev": reverses_on,
                "meta": json.dumps(
                    {
                        "po_number": po["po_number"],
                        "vendor_name": po["vendor_name"],
                        "journal_preview": build_accrual_journal_lines(amount=amt),
                        "reversal_preview": build_accrual_journal_lines(amount=amt, reverse=True),
                    }
                ),
            },
        )
        staged.append(
            {
                "id": str(cid),
                "source_kind": "unbilled_po",
                "description": desc,
                "amount": str(amt),
                "currency": str(po["currency"] or "USD")[:3],
                "period_end": period_end.isoformat(),
                "reverses_on": reverses_on.isoformat(),
                "status": "pending",
            }
        )

    # 2. Recurring posted bills from prior month not yet billed in current month
    prior_start = (period_end.replace(day=1) - timedelta(days=1)).replace(day=1)
    prior_end = period_end.replace(day=1) - timedelta(days=1)
    recurring_rows = (
        db.execute(
            text(
                """
                SELECT td.id, td.counterparty_name, td.total_amount, td.currency
                FROM app.trade_documents td
                WHERE td.business_id = :bid
                  AND td.kind = 'bill'
                  AND td.status IN ('posted', 'paid')
                  AND td.issue_date >= :pstart AND td.issue_date <= :pend
                  AND NOT EXISTS (
                      SELECT 1 FROM app.trade_documents cur
                      WHERE cur.business_id = :bid
                        AND cur.kind = 'bill'
                        AND lower(cur.counterparty_name) = lower(td.counterparty_name)
                        AND cur.issue_date > :pend AND cur.issue_date <= :period_end
                  )
                LIMIT 50
                """
            ),
            {
                "bid": business_id,
                "pstart": prior_start,
                "pend": prior_end,
                "period_end": period_end,
            },
        )
        .mappings()
        .all()
    )
    for bill in recurring_rows:
        amt = Decimal(str(bill["total_amount"])).quantize(Decimal("0.01"))
        if amt <= Decimal("0.00"):
            continue
        existing = db.execute(
            text(
                """
                SELECT id FROM app.accrual_candidates
                WHERE business_id = :bid AND period_end = :pe AND source_id = :sid
                LIMIT 1
                """
            ),
            {"bid": business_id, "pe": period_end, "sid": bill["id"]},
        ).scalar_one_or_none()
        if existing:
            continue
        cid = uuid4()
        desc = f"Recurring accrual for {bill['counterparty_name']} (prior-month bill)"
        db.execute(
            text(
                """
                INSERT INTO app.accrual_candidates
                    (id, business_id, period_end, source_kind, source_id, description,
                     amount, currency, reverses_on, status, metadata)
                VALUES
                    (:id, :bid, :pe, 'recurring_bill', :sid, :desc,
                     :amt, :ccy, :rev, 'pending', CAST(:meta AS jsonb))
                """
            ),
            {
                "id": cid,
                "bid": business_id,
                "pe": period_end,
                "sid": bill["id"],
                "desc": desc[:250],
                "amt": amt,
                "ccy": str(bill["currency"] or "USD")[:3],
                "rev": reverses_on,
                "meta": json.dumps(
                    {
                        "vendor_name": bill["counterparty_name"],
                        "journal_preview": build_accrual_journal_lines(amount=amt),
                        "reversal_preview": build_accrual_journal_lines(amount=amt, reverse=True),
                    }
                ),
            },
        )
        staged.append(
            {
                "id": str(cid),
                "source_kind": "recurring_bill",
                "description": desc,
                "amount": str(amt),
                "currency": str(bill["currency"] or "USD")[:3],
                "period_end": period_end.isoformat(),
                "reverses_on": reverses_on.isoformat(),
                "status": "pending",
            }
        )

    return staged


def list_accrual_candidates(db: Session, business_id: UUID) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            text(
                """
                SELECT id, period_end, source_kind, source_id, description,
                       amount, currency, reverses_on, status, posted_journal_id,
                       reversal_journal_id, metadata, created_at
                FROM app.accrual_candidates
                WHERE business_id = :bid
                ORDER BY period_end DESC, created_at DESC
                LIMIT 200
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": str(r["id"]),
            "period_end": r["period_end"].isoformat(),
            "source_kind": r["source_kind"],
            "source_id": str(r["source_id"]) if r["source_id"] else None,
            "description": r["description"],
            "amount": str(Decimal(str(r["amount"])).quantize(Decimal("0.01"))),
            "currency": r["currency"],
            "reverses_on": r["reverses_on"].isoformat(),
            "status": r["status"],
            "posted_journal_id": str(r["posted_journal_id"]) if r["posted_journal_id"] else None,
            "reversal_journal_id": str(r["reversal_journal_id"])
            if r["reversal_journal_id"]
            else None,
            "metadata": dict(r["metadata"] or {}),
        }
        for r in rows
    ]


def approve_accrual_candidate(
    db: Session,
    business_id: UUID,
    candidate_id: UUID,
) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                """
                SELECT id, amount, status, reverses_on, description
                FROM app.accrual_candidates
                WHERE business_id = :bid AND id = :cid
                FOR UPDATE
                """
            ),
            {"bid": business_id, "cid": candidate_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("ERR_NOT_FOUND", "Accrual candidate not found.", 404)
    if row["status"] in {"posted", "reversed"}:
        return {"id": str(row["id"]), "status": row["status"], "already_processed": True}
    db.execute(
        text(
            """
            UPDATE app.accrual_candidates
            SET status = 'posted'
            WHERE business_id = :bid AND id = :cid
            """
        ),
        {"bid": business_id, "cid": candidate_id},
    )
    return {
        "id": str(row["id"]),
        "status": "posted",
        "reverses_on": row["reverses_on"].isoformat(),
        "amount": str(Decimal(str(row["amount"])).quantize(Decimal("0.01"))),
        "journal_lines": build_accrual_journal_lines(amount=Decimal(str(row["amount"]))),
    }


def run_day1_auto_reversals(
    db: Session,
    business_id: UUID,
    as_of: date,
) -> dict[str, Any]:
    """Reverse all posted accruals whose reverses_on <= as_of (idempotent & I2-gated)."""
    rows = (
        db.execute(
            text(
                """
                SELECT id, amount, reverses_on, description
                FROM app.accrual_candidates
                WHERE business_id = :bid
                  AND status = 'posted'
                  AND reverses_on <= :as_of
                FOR UPDATE
                """
            ),
            {"bid": business_id, "as_of": as_of},
        )
        .mappings()
        .all()
    )
    reversed_ids: list[str] = []
    queued_for_approval: list[str] = []
    for r in rows:
        amt = Decimal(str(r["amount"])).quantize(Decimal("0.01"))
        allowed, _reason = should_auto_post(db, business_id, "accrual_day1_reversal", amt)
        if not allowed:
            queued_for_approval.append(str(r["id"]))
            continue
        db.execute(
            text(
                """
                UPDATE app.accrual_candidates
                SET status = 'reversed'
                WHERE business_id = :bid AND id = :cid AND status = 'posted'
                """
            ),
            {"bid": business_id, "cid": r["id"]},
        )
        reversed_ids.append(str(r["id"]))
    return {
        "as_of": as_of.isoformat(),
        "reversed_count": len(reversed_ids),
        "reversed_ids": reversed_ids,
        "queued_for_approval_count": len(queued_for_approval),
        "queued_for_approval_ids": queued_for_approval,
    }
