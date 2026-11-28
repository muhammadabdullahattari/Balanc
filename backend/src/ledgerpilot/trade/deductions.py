"""AR short-pay deduction classification & payment-terms discount validation (Phase L2)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from ledgerpilot.shared.errors import DomainError

_REASON_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "early_settlement_discount",
        re.compile(
            r"\b(early\s*pay|prompt\s*pay|2/10|1/10|1/15|settlement\s*discount|cash\s*discount)\b",
            re.I,
        ),
    ),
    (
        "freight_shortage",
        re.compile(
            r"\b(short\s*ship|shortage|missing\s*units?|freight|transit\s*loss|short\s*delivered)\b",
            re.I,
        ),
    ),
    (
        "damaged_goods",
        re.compile(
            r"\b(damage[d]?|broken|defective|spoiled|quality\s*claim|return\s*allowance)\b", re.I
        ),
    ),
    (
        "pricing_error",
        re.compile(
            r"\b(price\s*diff|pricing\s*error|overcharge[d]?|rate\s*discrepancy|billing\s*error)\b",
            re.I,
        ),
    ),
]


def parse_payment_terms(terms: str) -> dict[str, Any]:
    """Parse standard commercial payment terms such as '2/10 Net 30' or 'Net 30'."""
    raw = (terms or "").strip()
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*(?:,?\s*net\s*(\d+))?\s*$", raw, re.I)
    if m:
        return {
            "raw": raw,
            "discount_pct": Decimal(m.group(1)).quantize(Decimal("0.01")),
            "discount_days": int(m.group(2)),
            "net_days": int(m.group(3)) if m.group(3) else 30,
        }
    net_m = re.match(r"^\s*net\s*(\d+)\s*$", raw, re.I)
    if net_m:
        return {
            "raw": raw,
            "discount_pct": Decimal("0.00"),
            "discount_days": 0,
            "net_days": int(net_m.group(1)),
        }
    return {
        "raw": raw or "Net 30",
        "discount_pct": Decimal("0.00"),
        "discount_days": 0,
        "net_days": 30,
    }


def classify_short_pay_remittance(remittance_note: str) -> dict[str, Any]:
    """Classify a customer short-pay remittance note into a structured deduction reason."""
    note = (remittance_note or "").strip()
    for reason, pat in _REASON_RULES:
        if pat.search(note):
            return {
                "reason": reason,
                "confidence": "0.92",
                "source": "deterministic_rules",
            }
    return {
        "reason": "unknown",
        "confidence": "0.50",
        "source": "deterministic_rules",
    }


def evaluate_short_pay(
    *,
    invoice_number: str,
    customer_name: str,
    invoice_amount: Decimal,
    paid_amount: Decimal,
    invoice_date: date,
    payment_date: date,
    payment_terms: str = "Net 30",
    remittance_note: str = "",
    tone_profile: str = "friendly_firm",
) -> dict[str, Any]:
    """Evaluate a customer short-payment against contract terms and propose accounting action."""
    inv_amt = Decimal(str(invoice_amount)).quantize(Decimal("0.01"))
    paid_amt = Decimal(str(paid_amount)).quantize(Decimal("0.01"))
    if inv_amt <= Decimal("0.00") or paid_amt < Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Invoice and payment amounts must be valid.", 422)
    short_pay = (inv_amt - paid_amt).quantize(Decimal("0.01"))
    if short_pay <= Decimal("0.00"):
        return {
            "invoice_number": invoice_number,
            "short_pay_amount": "0.00",
            "reason": "paid_in_full",
            "in_terms_discount": False,
            "recommended_action": "close_invoice",
            "journal_lines": [],
            "dispute_email_draft": None,
        }

    terms = parse_payment_terms(payment_terms)
    classification = classify_short_pay_remittance(remittance_note)
    reason = classification["reason"]

    discount_pct: Decimal = terms["discount_pct"]
    discount_days: int = terms["discount_days"]
    discount_deadline = invoice_date + timedelta(days=discount_days)
    expected_discount = (
        (inv_amt * discount_pct / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
        if discount_pct > Decimal("0.00")
        else Decimal("0.00")
    )

    within_window = discount_days > 0 and payment_date <= discount_deadline
    amount_matches_discount = expected_discount > Decimal("0.00") and abs(
        short_pay - expected_discount
    ) <= Decimal("0.05")

    if within_window and amount_matches_discount:
        return {
            "invoice_number": invoice_number,
            "customer_name": customer_name,
            "invoice_amount": str(inv_amt),
            "paid_amount": str(paid_amt),
            "short_pay_amount": str(short_pay),
            "reason": "early_settlement_discount",
            "confidence": "0.98",
            "in_terms_discount": True,
            "discount_deadline": discount_deadline.isoformat(),
            "recommended_action": "post_sales_discount",
            "journal_lines": [
                {"account": "Operating Checking", "debit": str(paid_amt), "credit": "0.00"},
                {"account": "Sales Discounts", "debit": str(short_pay), "credit": "0.00"},
                {"account": "Accounts Receivable", "debit": "0.00", "credit": str(inv_amt)},
            ],
            "dispute_email_draft": None,
        }

    # Unauthorized/out-of-terms discount or dispute -> keep AR open + draft email
    if reason == "early_settlement_discount" and not within_window:
        dispute_reason = (
            f"early payment discount ({terms['raw']}) was taken after the "
            f"{discount_deadline.isoformat()} eligibility date"
        )
    elif reason == "early_settlement_discount" and not amount_matches_discount:
        dispute_reason = (
            f"deducted {short_pay} exceeds the contractual {discount_pct}% discount "
            f"({expected_discount})"
        )
    else:
        dispute_reason = f"short payment of {short_pay} classified as {reason.replace('_', ' ')}"

    greeting = "Hello" if tone_profile != "formal" else "Dear"
    dispute_email = {
        "subject": f"Balance remaining on Invoice {invoice_number} ({short_pay})",
        "tone_profile": tone_profile,
        "body": (
            f"{greeting} {customer_name},\n\n"
            f"Thank you for your payment of {paid_amt} on Invoice {invoice_number} "
            f"(original amount {inv_amt}). We noticed a remaining balance of {short_pay} "
            f"because {dispute_reason}.\n\n"
            f"Please remit the remaining {short_pay} or send supporting documentation so we can "
            f"update your account promptly.\n\nBest regards,\nAccounts Receivable"
        ),
    }

    return {
        "invoice_number": invoice_number,
        "customer_name": customer_name,
        "invoice_amount": str(inv_amt),
        "paid_amount": str(paid_amt),
        "short_pay_amount": str(short_pay),
        "reason": reason,
        "confidence": classification["confidence"],
        "in_terms_discount": False,
        "discount_deadline": discount_deadline.isoformat() if discount_days > 0 else None,
        "recommended_action": "leave_ar_open_and_dispute",
        "journal_lines": [
            {"account": "Operating Checking", "debit": str(paid_amt), "credit": "0.00"},
            {"account": "Accounts Receivable", "debit": "0.00", "credit": str(paid_amt)},
        ],
        "remaining_ar_balance": str(short_pay),
        "dispute_email_draft": dispute_email,
    }
