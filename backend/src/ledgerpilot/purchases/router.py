"""Phase C: Purchase Orders with price-variance gate & Business-Tone Collections Ladder."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import text

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business

router = APIRouter(prefix="/businesses/{business_id}", tags=["purchasing-and-collections"])


def _q2(val: Decimal | str | int | float) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"))


class PurchaseOrderLineInput(Schema):
    description: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrderCreate(Schema):
    number: str = Field(min_length=1, max_length=60)
    supplier_name: str = Field(min_length=1, max_length=160)
    party_id: UUID | None = None
    issue_date: date
    expected_date: date | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    tax_total: Decimal = Field(default=Decimal("0.00"), ge=0)
    variance_tolerance_pct: Decimal = Field(default=Decimal("5.00"), ge=0, le=100)
    lines: list[PurchaseOrderLineInput] = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=500)


class BillVarianceMatchInput(Schema):
    purchase_order_id: UUID | None = None
    po_number: str | None = None
    supplier_name: str | None = None
    bill_reference: str = Field(min_length=1, max_length=100)
    bill_id: UUID | None = None
    invoiced_amount: Decimal = Field(gt=0)


class VarianceDecisionInput(Schema):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=500)


class CollectionProfileInput(Schema):
    tone: Literal["friendly", "neutral", "firm"] = "friendly"
    sample_phrases: str = Field(default="", max_length=500)
    signature_name: str = Field(default="", max_length=120)
    phone_escalation_days: int = Field(default=60, ge=15, le=365)


class CompleteCallTaskInput(Schema):
    outcome_notes: str = Field(min_length=2, max_length=500)


@router.get("/purchase-orders")
def list_purchase_orders(
    business_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """List Purchase Orders and price-variance approval matches for the workspace."""
    authorize(db, identity, business_id)
    pos = (
        db.execute(
            text(
                """
                SELECT id, number, supplier_name, party_id, issue_date, expected_date,
                       currency, subtotal, tax_total, total, variance_tolerance_pct,
                       status, lines, notes, created_at
                FROM app.purchase_orders
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 100
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    matches = (
        db.execute(
            text(
                """
                SELECT m.id, m.purchase_order_id, po.number AS po_number,
                       po.supplier_name, m.bill_reference, m.invoiced_amount,
                       m.po_amount, m.variance_amount, m.variance_pct,
                       m.status, m.approval_reason, m.decided_at, m.created_at
                FROM app.purchase_order_matches m
                JOIN app.purchase_orders po
                  ON po.business_id = m.business_id AND po.id = m.purchase_order_id
                WHERE m.business_id = :bid
                ORDER BY m.created_at DESC
                LIMIT 100
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    return {
        "purchase_orders": [
            {
                "id": str(r["id"]),
                "number": str(r["number"]),
                "supplier_name": str(r["supplier_name"]),
                "issue_date": r["issue_date"].isoformat(),
                "expected_date": r["expected_date"].isoformat() if r["expected_date"] else None,
                "currency": str(r["currency"]),
                "subtotal": f"{_q2(r['subtotal']):.2f}",
                "tax_total": f"{_q2(r['tax_total']):.2f}",
                "total": f"{_q2(r['total']):.2f}",
                "variance_tolerance_pct": f"{Decimal(str(r['variance_tolerance_pct'])):.2f}",
                "status": str(r["status"]),
                "lines": list(r["lines"] or []),
                "notes": str(r["notes"] or ""),
            }
            for r in pos
        ],
        "matches": [
            {
                "id": str(m["id"]),
                "purchase_order_id": str(m["purchase_order_id"]),
                "po_number": str(m["po_number"]),
                "supplier_name": str(m["supplier_name"]),
                "bill_reference": str(m["bill_reference"]),
                "invoiced_amount": f"{_q2(m['invoiced_amount']):.2f}",
                "po_amount": f"{_q2(m['po_amount']):.2f}",
                "variance_amount": f"{_q2(m['variance_amount']):.2f}",
                "variance_pct": f"{Decimal(str(m['variance_pct'])):.2f}",
                "status": str(m["status"]),
                "approval_reason": str(m["approval_reason"] or ""),
                "requires_owner_approval": m["status"] == "pending_owner_approval",
                "created_at": m["created_at"].isoformat(),
            }
            for m in matches
        ],
    }


@router.post("/purchase-orders", status_code=201)
def create_purchase_order(
    business_id: UUID,
    body: PurchaseOrderCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Create an approved Purchase Order with line-item pricing and variance tolerance."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    subtotal = sum((_q2(ln.quantity * ln.unit_price) for ln in body.lines), Decimal("0.00"))
    tax_tot = _q2(body.tax_total)
    total = _q2(subtotal + tax_tot)
    if total <= Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Purchase order total must exceed zero.", 422)

    po_id = uuid4()
    serialized_lines = [
        {
            "description": ln.description.strip(),
            "quantity": str(ln.quantity),
            "unit_price": f"{_q2(ln.unit_price):.2f}",
            "line_total": f"{_q2(ln.quantity * ln.unit_price):.2f}",
        }
        for ln in body.lines
    ]
    db.execute(
        text(
            """
            INSERT INTO app.purchase_orders (
              id, business_id, number, party_id, supplier_name, issue_date,
              expected_date, currency, subtotal, tax_total, total,
              variance_tolerance_pct, status, lines, notes, approved_by, created_by
            ) VALUES (
              :id, :bid, :num, :pid, :sname, :idt,
              :edt, :ccy, :sub, :tax, :tot,
              :tol, 'approved', CAST(:lines AS jsonb), :notes, :uid, :uid
            )
            """
        ),
        {
            "id": po_id,
            "bid": business_id,
            "num": body.number.strip(),
            "pid": body.party_id,
            "sname": body.supplier_name.strip(),
            "idt": body.issue_date,
            "edt": body.expected_date,
            "ccy": body.currency.upper(),
            "sub": subtotal,
            "tax": tax_tot,
            "tot": total,
            "tol": body.variance_tolerance_pct,
            "lines": json.dumps(serialized_lines),
            "notes": body.notes.strip(),
            "uid": identity.user.id,
        },
    )
    record(db, identity.user.id, "purchase_order.created", business_id, po_id)
    return {
        "id": str(po_id),
        "number": body.number.strip(),
        "supplier_name": body.supplier_name.strip(),
        "currency": body.currency.upper(),
        "subtotal": f"{subtotal:.2f}",
        "tax_total": f"{tax_tot:.2f}",
        "total": f"{total:.2f}",
        "variance_tolerance_pct": f"{body.variance_tolerance_pct:.2f}",
        "status": "approved",
        "lines": serialized_lines,
    }


@router.post("/purchase-orders/match-bill", status_code=201)
def match_bill_against_purchase_order(
    business_id: UUID,
    body: BillVarianceMatchInput,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Match a supplier invoice against a Purchase Order and gate over-tolerance variances."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    po_row = None
    if body.purchase_order_id is not None:
        po_row = (
            db.execute(
                text("SELECT * FROM app.purchase_orders WHERE business_id = :bid AND id = :id"),
                {"bid": business_id, "id": body.purchase_order_id},
            )
            .mappings()
            .first()
        )
    elif body.po_number:
        po_row = (
            db.execute(
                text(
                    "SELECT * FROM app.purchase_orders "
                    "WHERE business_id = :bid AND upper(number) = :num LIMIT 1"
                ),
                {"bid": business_id, "num": body.po_number.strip().upper()},
            )
            .mappings()
            .first()
        )
    elif body.supplier_name:
        po_row = (
            db.execute(
                text(
                    """
                    SELECT * FROM app.purchase_orders
                    WHERE business_id = :bid
                      AND lower(supplier_name) = :sname
                      AND status IN ('approved', 'partially_matched')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"bid": business_id, "sname": body.supplier_name.strip().lower()},
            )
            .mappings()
            .first()
        )

    if po_row is None:
        raise DomainError("ERR_NOT_FOUND", "Matching open Purchase Order not found.", 404)

    po_amt = _q2(po_row["total"])
    inv_amt = _q2(body.invoiced_amount)
    var_amt = _q2(inv_amt - po_amt)
    var_pct = ((var_amt / po_amt) * Decimal("100")).quantize(Decimal("0.01"))
    tol_pct = Decimal(str(po_row["variance_tolerance_pct"])).quantize(Decimal("0.01"))

    needs_owner = var_pct > tol_pct
    match_status = "pending_owner_approval" if needs_owner else "matched_within_tolerance"
    match_id = uuid4()

    db.execute(
        text(
            """
            INSERT INTO app.purchase_order_matches (
              id, business_id, purchase_order_id, bill_id, bill_reference,
              invoiced_amount, po_amount, variance_amount, variance_pct, status
            ) VALUES (
              :id, :bid, :poid, :bid_doc, :bref,
              :inv, :po, :vamt, :vpct, :st
            )
            """
        ),
        {
            "id": match_id,
            "bid": business_id,
            "poid": po_row["id"],
            "bid_doc": body.bill_id,
            "bref": body.bill_reference.strip(),
            "inv": inv_amt,
            "po": po_amt,
            "vamt": var_amt,
            "vpct": var_pct,
            "st": match_status,
        },
    )
    if not needs_owner:
        db.execute(
            text(
                "UPDATE app.purchase_orders SET status = 'matched' "
                "WHERE business_id = :bid AND id = :id"
            ),
            {"bid": business_id, "id": po_row["id"]},
        )

    ccy = str(po_row["currency"])
    explanation = (
        f"Invoice {body.bill_reference} is {var_pct:.2f}% higher than {po_row['number']} "
        f"({ccy} {inv_amt:.2f} vs {ccy} {po_amt:.2f}, tolerance {tol_pct:.2f}%) — "
        "owner approval is required before posting."
        if needs_owner
        else (
            f"Invoice {body.bill_reference} matches {po_row['number']} within the "
            f"{tol_pct:.2f}% price tolerance ({var_pct:.2f}% variance)."
        )
    )
    return {
        "id": str(match_id),
        "purchase_order_id": str(po_row["id"]),
        "po_number": str(po_row["number"]),
        "supplier_name": str(po_row["supplier_name"]),
        "bill_reference": body.bill_reference.strip(),
        "po_amount": f"{po_amt:.2f}",
        "invoiced_amount": f"{inv_amt:.2f}",
        "variance_amount": f"{var_amt:.2f}",
        "variance_pct": f"{var_pct:.2f}",
        "tolerance_pct": f"{tol_pct:.2f}",
        "status": match_status,
        "requires_owner_approval": needs_owner,
        "message": explanation,
    }


@router.post("/purchase-orders/matches/{match_id}/decide")
def decide_purchase_order_variance(
    business_id: UUID,
    match_id: UUID,
    body: VarianceDecisionInput,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Owner-only gate to approve or reject an over-PO supplier invoice variance."""
    authorize(db, identity, business_id, {"owner"})
    row = (
        db.execute(
            text(
                "SELECT id, purchase_order_id, status FROM app.purchase_order_matches "
                "WHERE business_id = :bid AND id = :id FOR UPDATE"
            ),
            {"bid": business_id, "id": match_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DomainError("ERR_NOT_FOUND", "Purchase order variance record not found.", 404)

    new_status = "owner_approved" if body.decision == "approve" else "rejected"
    db.execute(
        text(
            """
            UPDATE app.purchase_order_matches
            SET status = :st,
                approval_reason = :reason,
                decided_by = :uid,
                decided_at = now()
            WHERE business_id = :bid AND id = :id
            """
        ),
        {
            "st": new_status,
            "reason": body.reason.strip(),
            "uid": identity.user.id,
            "bid": business_id,
            "id": match_id,
        },
    )
    if new_status == "owner_approved":
        db.execute(
            text(
                "UPDATE app.purchase_orders SET status = 'matched' "
                "WHERE business_id = :bid AND id = :id"
            ),
            {"bid": business_id, "id": row["purchase_order_id"]},
        )
    record(db, identity.user.id, f"purchase_order.variance_{body.decision}d", business_id, match_id)
    return {
        "id": str(match_id),
        "status": new_status,
        "approval_reason": body.reason.strip(),
        "decided_by": str(identity.user.id),
    }


def _compose_toned_reminder(
    *,
    tone: str,
    customer_name: str,
    invoice_number: str,
    amount_due: Decimal,
    currency: str,
    days_overdue: int,
    sample_phrases: str,
    signature_name: str,
) -> str:
    amt_str = f"{currency} {amount_due:,.2f}"
    sig = signature_name.strip() or "Accounts Team"
    custom = f" {sample_phrases.strip()}" if sample_phrases.strip() else ""
    if tone == "firm" or days_overdue >= 30:
        return (
            f"Dear {customer_name}, invoice {invoice_number} for {amt_str} is now "
            f"{days_overdue} days past due. Please arrange settlement promptly or confirm "
            f"your payment date today.{custom} — {sig}"
        )
    if tone == "neutral" or days_overdue >= 15:
        return (
            f"Hello {customer_name}, following up on invoice {invoice_number} ({amt_str}), "
            f"currently {days_overdue} days overdue. Please let us know when payment is scheduled."
            f"{custom} — {sig}"
        )
    return (
        f"Hi {customer_name}, hope your week is going well! Just a friendly reminder that "
        f"invoice {invoice_number} for {amt_str} is {days_overdue} days past its due date."
        f"{custom} — {sig}"
    )


@router.get("/collections")
def get_collections_overview(
    business_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Return workspace tone profile and overdue phone-call escalation tasks."""
    authorize(db, identity, business_id)
    biz = get_business(db, business_id)
    prof = (
        db.execute(
            text("SELECT * FROM app.collection_profiles WHERE business_id = :bid"),
            {"bid": business_id},
        )
        .mappings()
        .first()
    )
    tasks = (
        db.execute(
            text(
                """
                SELECT id, invoice_id, invoice_number, customer_name, amount_due,
                       currency, days_overdue, task_kind, status, outcome_notes,
                       completed_at, created_at
                FROM app.collection_tasks
                WHERE business_id = :bid
                ORDER BY status ASC, days_overdue DESC, created_at DESC
                LIMIT 100
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    profile_data = (
        {
            "tone": str(prof["tone"]),
            "sample_phrases": str(prof["sample_phrases"] or ""),
            "signature_name": str(prof["signature_name"] or biz.name),
            "phone_escalation_days": int(prof["phone_escalation_days"]),
        }
        if prof
        else {
            "tone": "friendly",
            "sample_phrases": "",
            "signature_name": biz.name,
            "phone_escalation_days": 60,
        }
    )
    return {
        "profile": profile_data,
        "tasks": [
            {
                "id": str(t["id"]),
                "invoice_id": str(t["invoice_id"]) if t["invoice_id"] else None,
                "invoice_number": str(t["invoice_number"]),
                "customer_name": str(t["customer_name"]),
                "amount_due": f"{_q2(t['amount_due']):.2f}",
                "currency": str(t["currency"]),
                "days_overdue": int(t["days_overdue"]),
                "task_kind": str(t["task_kind"]),
                "status": str(t["status"]),
                "outcome_notes": str(t["outcome_notes"] or ""),
                "completed_at": t["completed_at"].isoformat() if t["completed_at"] else None,
            }
            for t in tasks
        ],
    }


@router.post("/collections/profile")
def upsert_collection_profile(
    business_id: UUID,
    body: CollectionProfileInput,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Configure the business's own reminder tone and overdue phone-call escalation threshold."""
    authorize(db, identity, business_id, {"owner", "accountant"})
    ladder = [
        {"min_days": 0, "action": body.tone},
        {"min_days": 15, "action": "neutral" if body.tone == "friendly" else "firm"},
        {"min_days": 30, "action": "firm"},
        {"min_days": body.phone_escalation_days, "action": "phone_call_task"},
    ]
    db.execute(
        text(
            """
            INSERT INTO app.collection_profiles (
              id, business_id, tone, sample_phrases, signature_name,
              phone_escalation_days, escalation_ladder, updated_by, updated_at
            ) VALUES (
              :id, :bid, :tone, :phrases, :sig,
              :pdays, CAST(:ladder AS jsonb), :uid, now()
            )
            ON CONFLICT (business_id) DO UPDATE SET
              tone = EXCLUDED.tone,
              sample_phrases = EXCLUDED.sample_phrases,
              signature_name = EXCLUDED.signature_name,
              phone_escalation_days = EXCLUDED.phone_escalation_days,
              escalation_ladder = EXCLUDED.escalation_ladder,
              updated_by = EXCLUDED.updated_by,
              updated_at = now()
            """
        ),
        {
            "id": uuid4(),
            "bid": business_id,
            "tone": body.tone,
            "phrases": body.sample_phrases.strip(),
            "sig": body.signature_name.strip(),
            "pdays": body.phone_escalation_days,
            "ladder": json.dumps(ladder),
            "uid": identity.user.id,
        },
    )
    return {
        "tone": body.tone,
        "sample_phrases": body.sample_phrases.strip(),
        "signature_name": body.signature_name.strip(),
        "phone_escalation_days": body.phone_escalation_days,
        "escalation_ladder": ladder,
    }


@router.post("/collections/run-ladder")
def run_collections_escalation_ladder(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate overdue receivables and escalate 60+ day items to phone call tasks."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    payload = body or {}
    as_of_str = str(payload.get("as_of") or date.today().isoformat())
    as_of_dt = date.fromisoformat(as_of_str)

    prof = (
        db.execute(
            text("SELECT * FROM app.collection_profiles WHERE business_id = :bid"),
            {"bid": business_id},
        )
        .mappings()
        .first()
    )
    base_tone = str(prof["tone"]) if prof else "friendly"
    phrases = str(prof["sample_phrases"] or "") if prof else ""
    sig = str(prof["signature_name"] or biz.name) if prof else biz.name
    esc_days = int(prof["phone_escalation_days"]) if prof else 60

    overdue_items: list[dict[str, Any]] = []
    if isinstance(payload.get("invoices"), list):
        for item in payload["invoices"]:
            if isinstance(item, dict):
                overdue_items.append(item)
    else:
        rows = (
            db.execute(
                text(
                    """
                    SELECT td.id, td.number, p.name AS customer_name, td.due_date, td.currency,
                           td.total - coalesce((
                             SELECT sum(al.amount) FROM app.allocations al
                             WHERE al.business_id = td.business_id AND al.document_id = td.id
                           ), 0) AS outstanding
                    FROM app.trade_documents td
                    LEFT JOIN app.parties p
                      ON p.business_id = td.business_id AND p.id = td.party_id
                    WHERE td.business_id = :bid
                      AND td.kind = 'invoice'
                      AND td.due_date IS NOT NULL
                      AND td.due_date < :as_of
                    ORDER BY td.due_date ASC
                    LIMIT 100
                    """
                ),
                {"bid": business_id, "as_of": as_of_dt},
            )
            .mappings()
            .all()
        )
        for r in rows:
            out_amt = _q2(r["outstanding"])
            if out_amt > Decimal("0.00"):
                overdue_items.append(
                    {
                        "invoice_id": str(r["id"]),
                        "invoice_number": str(r["number"]),
                        "customer_name": str(r["customer_name"] or "Customer"),
                        "amount_due": f"{out_amt:.2f}",
                        "currency": str(r["currency"] or biz.currency),
                        "days_overdue": (as_of_dt - r["due_date"]).days,
                    }
                )

    reminders_prepared: list[dict[str, Any]] = []
    phone_tasks_created: list[dict[str, Any]] = []

    for inv in overdue_items:
        days_late = int(inv.get("days_overdue") or 0)
        if days_late <= 0:
            continue
        inv_num = str(inv.get("invoice_number") or "INV")
        cust = str(inv.get("customer_name") or "Customer")
        amt = _q2(inv.get("amount_due") or "0.00")
        ccy = str(inv.get("currency") or biz.currency).upper()
        inv_uuid = UUID(str(inv["invoice_id"])) if inv.get("invoice_id") else None

        if days_late >= esc_days:
            task_id = uuid4()
            db.execute(
                text(
                    """
                    INSERT INTO app.collection_tasks (
                      id, business_id, invoice_id, invoice_number, customer_name,
                      amount_due, currency, days_overdue, task_kind, status, assigned_to
                    ) VALUES (
                      :id, :bid, :iid, :inum, :cname,
                      :amt, :ccy, :days, 'phone_call', 'open', :uid
                    )
                    ON CONFLICT (business_id, invoice_number, task_kind) DO UPDATE SET
                      days_overdue = EXCLUDED.days_overdue,
                      amount_due = EXCLUDED.amount_due
                    """
                ),
                {
                    "id": task_id,
                    "bid": business_id,
                    "iid": inv_uuid,
                    "inum": inv_num,
                    "cname": cust,
                    "amt": amt,
                    "ccy": ccy,
                    "days": days_late,
                    "uid": identity.user.id,
                },
            )
            phone_tasks_created.append(
                {
                    "invoice_number": inv_num,
                    "customer_name": cust,
                    "amount_due": f"{amt:.2f}",
                    "currency": ccy,
                    "days_overdue": days_late,
                    "action": "phone_call_task",
                }
            )
        else:
            effective_tone = (
                "firm"
                if days_late >= 30
                else ("neutral" if days_late >= 15 and base_tone == "friendly" else base_tone)
            )
            msg = _compose_toned_reminder(
                tone=effective_tone,
                customer_name=cust,
                invoice_number=inv_num,
                amount_due=amt,
                currency=ccy,
                days_overdue=days_late,
                sample_phrases=phrases,
                signature_name=sig,
            )
            reminders_prepared.append(
                {
                    "invoice_number": inv_num,
                    "customer_name": cust,
                    "amount_due": f"{amt:.2f}",
                    "currency": ccy,
                    "days_overdue": days_late,
                    "tone_used": effective_tone,
                    "message": msg,
                }
            )

    return {
        "as_of": as_of_dt.isoformat(),
        "phone_escalation_days": esc_days,
        "reminders_prepared": reminders_prepared,
        "phone_tasks_created": phone_tasks_created,
    }


@router.post("/collections/tasks/{task_id}/complete")
def complete_collection_call_task(
    business_id: UUID,
    task_id: UUID,
    body: CompleteCallTaskInput,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Record phone-call outcome notes and mark the 60-day escalation task completed."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    updated = (
        db.execute(
            text(
                """
            UPDATE app.collection_tasks
            SET status = 'completed',
                outcome_notes = :notes,
                completed_at = now()
            WHERE business_id = :bid AND id = :id
            RETURNING id, invoice_number, status
            """
            ),
            {"notes": body.outcome_notes.strip(), "bid": business_id, "id": task_id},
        )
        .mappings()
        .first()
    )
    if updated is None:
        raise DomainError("ERR_NOT_FOUND", "Collection call task not found.", 404)
    return {
        "id": str(updated["id"]),
        "invoice_number": str(updated["invoice_number"]),
        "status": str(updated["status"]),
        "outcome_notes": body.outcome_notes.strip(),
    }
