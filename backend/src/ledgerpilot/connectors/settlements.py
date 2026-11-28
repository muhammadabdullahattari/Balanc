"""PayPal, Amazon, Stripe, Shopify & Square settlement unbundling with reserves (Phase M2)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any

from ledgerpilot.shared.errors import DomainError


def _dec(val: Any) -> Decimal:
    raw = str(val or "0").strip().replace("$", "").replace(",", "")
    if not raw or raw in {"-", "N/A"}:
        return Decimal("0.00")
    try:
        return Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def unbundle_settlement_rows(
    *,
    provider: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Unbundle gateway payout rows into Gross Sales, Fees, Refunds, Reserve Holds & Net Payout."""
    prov = provider.strip().lower()
    if prov not in {"stripe", "shopify", "square", "paypal", "amazon"}:
        raise DomainError(
            "ERR_VALIDATION",
            "Provider must be stripe, shopify, square, paypal, or amazon.",
            422,
        )
    if not rows:
        raise DomainError("ERR_VALIDATION", "Settlement file contains no rows.", 422)

    gross_sales = Decimal("0.00")
    processing_fees = Decimal("0.00")
    refunds = Decimal("0.00")
    reserve_holds = Decimal("0.00")
    reserve_releases = Decimal("0.00")

    for r in rows:
        lower = {str(k).strip().lower(): v for k, v in r.items()}
        kind = str(lower.get("type") or lower.get("transaction_type") or "sale").strip().lower()
        amt = _dec(lower.get("gross") or lower.get("amount") or lower.get("total"))
        fee = abs(_dec(lower.get("fee") or lower.get("fees") or lower.get("commission")))
        refund = abs(_dec(lower.get("refund") or lower.get("refunds")))
        reserve_hold = abs(_dec(lower.get("reserve_hold") or lower.get("reserve")))
        reserve_rel = abs(_dec(lower.get("reserve_release")))

        if kind in {"reserve_hold", "rolling_reserve", "hold"}:
            reserve_holds += abs(amt) if reserve_hold == Decimal("0.00") else reserve_hold
        elif kind in {"reserve_release", "release"}:
            reserve_releases += abs(amt) if reserve_rel == Decimal("0.00") else reserve_rel
        elif kind in {"refund", "chargeback", "return"}:
            refunds += abs(amt) if refund == Decimal("0.00") else refund
            processing_fees += fee
        else:
            gross_sales += max(Decimal("0.00"), amt)
            processing_fees += fee
            refunds += refund
            reserve_holds += reserve_hold
            reserve_releases += reserve_rel

    net_payout = (
        gross_sales - processing_fees - refunds - reserve_holds + reserve_releases
    ).quantize(Decimal("0.01"))

    lines: list[dict[str, str]] = []
    if net_payout > Decimal("0.00"):
        lines.append(
            {
                "account": f"Operating Checking ({prov.title()} Payout)",
                "debit": str(net_payout),
                "credit": "0.00",
            }
        )
    elif net_payout < Decimal("0.00"):
        lines.append(
            {
                "account": f"Operating Checking ({prov.title()} Clawback)",
                "debit": "0.00",
                "credit": str(abs(net_payout)),
            }
        )

    if processing_fees > Decimal("0.00"):
        lines.append(
            {
                "account": f"{prov.title()} Processing Fees",
                "debit": str(processing_fees),
                "credit": "0.00",
            }
        )
    if refunds > Decimal("0.00"):
        lines.append(
            {"account": "Sales Returns & Refunds", "debit": str(refunds), "credit": "0.00"}
        )
    if reserve_holds > Decimal("0.00"):
        lines.append(
            {
                "account": "Merchant Reserve Receivable",
                "debit": str(reserve_holds),
                "credit": "0.00",
            }
        )

    if gross_sales > Decimal("0.00"):
        lines.append(
            {
                "account": f"{prov.title()} Gross Sales Revenue",
                "debit": "0.00",
                "credit": str(gross_sales),
            }
        )
    if reserve_releases > Decimal("0.00"):
        lines.append(
            {
                "account": "Merchant Reserve Receivable",
                "debit": "0.00",
                "credit": str(reserve_releases),
            }
        )

    total_debits = sum((Decimal(ln["debit"]) for ln in lines), Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    total_credits = sum((Decimal(ln["credit"]) for ln in lines), Decimal("0.00")).quantize(
        Decimal("0.01")
    )

    return {
        "provider": prov,
        "row_count": len(rows),
        "gross_sales": str(gross_sales.quantize(Decimal("0.01"))),
        "processing_fees": str(processing_fees.quantize(Decimal("0.01"))),
        "refunds": str(refunds.quantize(Decimal("0.01"))),
        "reserve_holds": str(reserve_holds.quantize(Decimal("0.01"))),
        "reserve_releases": str(reserve_releases.quantize(Decimal("0.01"))),
        "net_payout": str(net_payout),
        "total_debits": str(total_debits),
        "total_credits": str(total_credits),
        "balanced": total_debits == total_credits,
        "journal_lines": lines,
    }


def unbundle_settlement_csv(provider: str, csv_text: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    rows = [dict(r) for r in reader]
    return unbundle_settlement_rows(provider=provider, rows=rows)
