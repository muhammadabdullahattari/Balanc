"""Prepaid expense & deferred revenue auto-amortization engine (Phase K2 / Module 2)."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.auto_post import should_auto_post


def _add_months(start: date, months: int) -> date:
    month_idx = (start.month - 1) + months
    year = start.year + month_idx // 12
    month = (month_idx % 12) + 1
    day = min(
        start.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return date(year, month, day)


def count_service_months(start_date: date, end_date: date) -> int:
    """Count inclusive calendar months spanned by a service period (minimum 1)."""
    if end_date < start_date:
        raise DomainError("ERR_VALIDATION", "Service end_date must be on or after start_date.", 422)
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if end_date.day >= start_date.day - 1:
        months += 1
    return max(1, months)


def compute_amortization_installments(
    total_amount: Decimal,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Split total_amount across monthly periods (ROUND_HALF_EVEN, remainder on last period)."""
    total = Decimal(str(total_amount)).quantize(Decimal("0.01"))
    if total <= Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Amortization total_amount must be positive.", 422)
    n_months = count_service_months(start_date, end_date)
    base = (total / Decimal(n_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    installments: list[dict[str, Any]] = []
    running = Decimal("0.00")
    for idx in range(n_months):
        period_date = _add_months(start_date, idx)
        if idx == n_months - 1:
            amt = (total - running).quantize(Decimal("0.01"))
        else:
            amt = base
            running += amt
        installments.append(
            {
                "period_number": idx + 1,
                "recognition_date": period_date.isoformat(),
                "amount": str(amt),
            }
        )
    return installments


def create_amortization_schedule(
    db: Session,
    business_id: UUID,
    *,
    schedule_type: str,
    total_amount: Decimal,
    start_date: date,
    end_date: date,
    source_document_id: UUID | None = None,
    asset_or_liability_account_id: UUID | None = None,
    recognition_account_id: UUID | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    if schedule_type not in {"prepaid_expense", "deferred_revenue"}:
        raise DomainError(
            "ERR_VALIDATION",
            "schedule_type must be 'prepaid_expense' or 'deferred_revenue'.",
            422,
        )
    installments = compute_amortization_installments(total_amount, start_date, end_date)
    total = Decimal(str(total_amount)).quantize(Decimal("0.01"))
    sid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.amortization_schedules
                (id, business_id, source_document_id, schedule_type, total_amount,
                 recognized_amount, currency, start_date, end_date, interval,
                 asset_or_liability_account_id, recognition_account_id, status)
            VALUES
                (:id, :bid, :doc_id, :stype, :total, 0, :ccy, :sdate, :edate,
                 'monthly', :al_acc, :rec_acc, 'active')
            """
        ),
        {
            "id": sid,
            "bid": business_id,
            "doc_id": source_document_id,
            "stype": schedule_type,
            "total": total,
            "ccy": currency.upper()[:3],
            "sdate": start_date,
            "edate": end_date,
            "al_acc": asset_or_liability_account_id,
            "rec_acc": recognition_account_id,
        },
    )
    return {
        "id": str(sid),
        "schedule_type": schedule_type,
        "total_amount": str(total),
        "recognized_amount": "0.00",
        "remaining_amount": str(total),
        "currency": currency.upper()[:3],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "status": "active",
        "installment_count": len(installments),
        "installments": installments,
    }


def list_amortization_schedules(db: Session, business_id: UUID) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            text(
                """
                SELECT id, source_document_id, schedule_type, total_amount, recognized_amount,
                       currency, start_date, end_date, interval, status, created_at
                FROM app.amortization_schedules
                WHERE business_id = :bid
                ORDER BY start_date DESC, created_at DESC
                LIMIT 200
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    results: list[dict[str, Any]] = []
    for r in rows:
        tot = Decimal(str(r["total_amount"])).quantize(Decimal("0.01"))
        rec = Decimal(str(r["recognized_amount"])).quantize(Decimal("0.01"))
        results.append(
            {
                "id": str(r["id"]),
                "source_document_id": str(r["source_document_id"])
                if r["source_document_id"]
                else None,
                "schedule_type": r["schedule_type"],
                "total_amount": str(tot),
                "recognized_amount": str(rec),
                "remaining_amount": str((tot - rec).quantize(Decimal("0.01"))),
                "currency": r["currency"],
                "start_date": r["start_date"].isoformat(),
                "end_date": r["end_date"].isoformat(),
                "interval": r["interval"],
                "status": r["status"],
            }
        )
    return results


def run_monthly_amortization(
    db: Session,
    business_id: UUID,
    as_of: date,
) -> dict[str, Any]:
    """Recognize due monthly installments across active schedules (I2 auto-post gated)."""
    rows = (
        db.execute(
            text(
                """
                SELECT id, schedule_type, total_amount, recognized_amount, start_date, end_date
                FROM app.amortization_schedules
                WHERE business_id = :bid AND status = 'active' AND start_date <= :as_of
                FOR UPDATE
                """
            ),
            {"bid": business_id, "as_of": as_of},
        )
        .mappings()
        .all()
    )
    posted: list[dict[str, Any]] = []
    staged: list[dict[str, Any]] = []
    for r in rows:
        tot = Decimal(str(r["total_amount"])).quantize(Decimal("0.01"))
        rec = Decimal(str(r["recognized_amount"])).quantize(Decimal("0.01"))
        installments = compute_amortization_installments(tot, r["start_date"], r["end_date"])
        due_cumulative = sum(
            (
                Decimal(i["amount"])
                for i in installments
                if date.fromisoformat(i["recognition_date"]) <= as_of
            ),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        delta = (due_cumulative - rec).quantize(Decimal("0.01"))
        if delta <= Decimal("0.00"):
            continue
        allowed, _reason = should_auto_post(db, business_id, "amortization_monthly", delta)
        entry = {
            "schedule_id": str(r["id"]),
            "schedule_type": r["schedule_type"],
            "amount": str(delta),
            "as_of": as_of.isoformat(),
        }
        if not allowed:
            staged.append(entry)
            continue
        new_rec = (rec + delta).quantize(Decimal("0.01"))
        new_status = "completed" if new_rec >= tot else "active"
        db.execute(
            text(
                """
                UPDATE app.amortization_schedules
                SET recognized_amount = :rec, status = :st
                WHERE business_id = :bid AND id = :sid
                """
            ),
            {"rec": new_rec, "st": new_status, "bid": business_id, "sid": r["id"]},
        )
        entry["status"] = new_status
        posted.append(entry)
    return {
        "as_of": as_of.isoformat(),
        "posted_count": len(posted),
        "posted": posted,
        "staged_for_review_count": len(staged),
        "staged_for_review": staged,
    }
