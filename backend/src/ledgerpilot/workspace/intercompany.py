"""Multi-entity intercompany mirror posting & elimination reconciliation (Phase M3 / Module 6)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError


def verify_dual_membership(
    db: Session,
    user_id: UUID,
    business_a_id: UUID,
    business_b_id: UUID,
) -> None:
    """Ensure the acting user holds active owner/accountant membership in BOTH businesses."""
    if business_a_id == business_b_id:
        raise DomainError(
            "ERR_VALIDATION", "Intercompany link requires two distinct businesses.", 422
        )
    rows = (
        db.execute(
            text(
                """
                SELECT business_id, role
                FROM app.memberships
                WHERE user_id = :uid
                  AND business_id IN (:ba, :bb)
                  AND active IS TRUE
                  AND role IN ('owner', 'accountant')
                """
            ),
            {"uid": user_id, "ba": business_a_id, "bb": business_b_id},
        )
        .mappings()
        .all()
    )
    found = {r["business_id"] for r in rows}
    if business_a_id not in found or business_b_id not in found:
        raise DomainError(
            "ERR_FORBIDDEN",
            "Owner or accountant membership in both businesses is required for intercompany links.",
            403,
        )


def create_intercompany_link(
    db: Session,
    *,
    user_id: UUID,
    business_id: UUID,
    counterparty_business_id: UUID,
    due_from_account_id: UUID | None = None,
    due_to_account_id: UUID | None = None,
) -> dict[str, Any]:
    verify_dual_membership(db, user_id, business_id, counterparty_business_id)
    link_id = uuid4()
    row = (
        db.execute(
            text(
                """
                INSERT INTO app.intercompany_links
                    (id, business_id, counterparty_business_id, due_from_account_id,
                     due_to_account_id, active)
                VALUES (:id, :bid, :cbid, :df, :dt, TRUE)
                ON CONFLICT (business_id, counterparty_business_id)
                DO UPDATE SET
                    due_from_account_id = COALESCE(
                        EXCLUDED.due_from_account_id,
                        app.intercompany_links.due_from_account_id
                    ),
                    due_to_account_id = COALESCE(
                        EXCLUDED.due_to_account_id,
                        app.intercompany_links.due_to_account_id
                    ),
                    active = TRUE
                RETURNING id, business_id, counterparty_business_id, active
                """
            ),
            {
                "id": link_id,
                "bid": business_id,
                "cbid": counterparty_business_id,
                "df": due_from_account_id,
                "dt": due_to_account_id,
            },
        )
        .mappings()
        .one()
    )
    return {
        "id": str(row["id"]),
        "business_id": str(row["business_id"]),
        "counterparty_business_id": str(row["counterparty_business_id"]),
        "active": bool(row["active"]),
    }


def build_intercompany_mirror_entries(
    *,
    entity_a_name: str,
    entity_b_name: str,
    amount: Decimal,
    currency: str = "USD",
    fx_rate_to_b: Decimal = Decimal("1.000000"),
    description: str = "Intercompany management fee",
    posting_date: date | None = None,
) -> dict[str, Any]:
    """Build balanced Entity A (Due From) and Entity B (Due To) mirror + elimination entries."""
    amt_a = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amt_a <= Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Intercompany amount must be positive.", 422)
    amt_b = (amt_a * Decimal(str(fx_rate_to_b))).quantize(Decimal("0.01"))
    pdate = (posting_date or date.today()).isoformat()
    return {
        "posting_date": pdate,
        "description": description,
        "currency": currency.upper()[:3],
        "entity_a_entry": {
            "entity": entity_a_name,
            "lines": [
                {"account": f"Due From {entity_b_name}", "debit": str(amt_a), "credit": "0.00"},
                {"account": "Intercompany Revenue", "debit": "0.00", "credit": str(amt_a)},
            ],
        },
        "entity_b_mirror_entry": {
            "entity": entity_b_name,
            "lines": [
                {"account": "Intercompany Expense", "debit": str(amt_b), "credit": "0.00"},
                {"account": f"Due To {entity_a_name}", "debit": "0.00", "credit": str(amt_b)},
            ],
        },
        "elimination_entry": {
            "lines": [
                {"account": "Intercompany Revenue", "debit": str(amt_a), "credit": "0.00"},
                {"account": "Intercompany Expense", "debit": "0.00", "credit": str(amt_a)},
                {"account": f"Due To {entity_a_name}", "debit": str(amt_a), "credit": "0.00"},
                {"account": f"Due From {entity_b_name}", "debit": "0.00", "credit": str(amt_a)},
            ],
            "eliminates_to_zero": True,
        },
    }


def record_intercompany_pair(
    db: Session,
    *,
    business_id: UUID,
    counterparty_business_id: UUID,
    source_journal_id: UUID,
    amount: Decimal,
    currency: str = "USD",
    fx_difference: Decimal = Decimal("0.00"),
) -> dict[str, Any]:
    pid = uuid4()
    amt = Decimal(str(amount)).quantize(Decimal("0.01"))
    fx_diff = Decimal(str(fx_difference)).quantize(Decimal("0.01"))
    status = "fx_mismatch" if fx_diff != Decimal("0.00") else "matched"
    row = (
        db.execute(
            text(
                """
                INSERT INTO app.intercompany_pairs
                    (id, business_id, counterparty_business_id, source_journal_id,
                     amount, currency, fx_difference, status)
                VALUES (:id, :bid, :cbid, :sjid, :amt, :ccy, :fxd, :st)
                ON CONFLICT (business_id, source_journal_id)
                DO UPDATE SET status = EXCLUDED.status, fx_difference = EXCLUDED.fx_difference
                RETURNING id, status, amount, currency, fx_difference
                """
            ),
            {
                "id": pid,
                "bid": business_id,
                "cbid": counterparty_business_id,
                "sjid": source_journal_id,
                "amt": amt,
                "ccy": currency.upper()[:3],
                "fxd": fx_diff,
                "st": status,
            },
        )
        .mappings()
        .one()
    )
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "amount": str(Decimal(str(row["amount"])).quantize(Decimal("0.01"))),
        "currency": row["currency"],
        "fx_difference": str(Decimal(str(row["fx_difference"])).quantize(Decimal("0.01"))),
    }


def list_intercompany_pairs(db: Session, business_id: UUID) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            text(
                """
                SELECT id, counterparty_business_id, source_journal_id, mirror_draft_id,
                       mirror_journal_id, amount, currency, fx_difference, status, created_at
                FROM app.intercompany_pairs
                WHERE business_id = :bid
                ORDER BY created_at DESC
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
            "counterparty_business_id": str(r["counterparty_business_id"]),
            "source_journal_id": str(r["source_journal_id"]),
            "mirror_draft_id": str(r["mirror_draft_id"]) if r["mirror_draft_id"] else None,
            "mirror_journal_id": str(r["mirror_journal_id"]) if r["mirror_journal_id"] else None,
            "amount": str(Decimal(str(r["amount"])).quantize(Decimal("0.01"))),
            "currency": r["currency"],
            "fx_difference": str(Decimal(str(r["fx_difference"])).quantize(Decimal("0.01"))),
            "status": r["status"],
        }
        for r in rows
    ]
