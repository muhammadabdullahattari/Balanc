"""Multi-SKU Landed Cost Proration (Weight, Volume, Value, Quantity) (Phase N3 / Module 13)."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError

VALID_BASES = {"weight", "volume", "value", "quantity"}


def prorate_landed_cost(
    *,
    total_landed_cost: Decimal,
    allocation_basis: str,
    sku_lines: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prorate freight/customs/duty/insurance across multi-SKU lines with penny conservation."""
    basis = allocation_basis.strip().lower()
    if basis not in VALID_BASES:
        raise DomainError(
            "ERR_VALIDATION",
            f"allocation_basis must be one of {sorted(VALID_BASES)}.",
            422,
        )
    total_cost = Decimal(str(total_landed_cost)).quantize(Decimal("0.01"))
    if total_cost <= Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "total_landed_cost must be positive.", 422)
    if not sku_lines:
        raise DomainError("ERR_VALIDATION", "At least one SKU line is required.", 422)

    weights: list[Decimal] = []
    for ln in sku_lines:
        qty = Decimal(str(ln.get("quantity") or "1"))
        if qty <= Decimal("0"):
            raise DomainError("ERR_VALIDATION", "SKU quantity must be positive.", 422)
        if basis == "quantity":
            w = qty
        elif basis == "value":
            unit_cost = Decimal(str(ln.get("unit_cost") or "0"))
            w = unit_cost * qty
        elif basis == "weight":
            w = Decimal(str(ln.get("unit_weight") or "0")) * qty
        else:
            w = Decimal(str(ln.get("unit_volume") or "0")) * qty
        weights.append(w)

    total_weight = sum(weights, Decimal("0"))
    if total_weight <= Decimal("0"):
        raise DomainError(
            "ERR_VALIDATION",
            f"Sum of {basis} across SKU lines must be positive.",
            422,
        )

    shares: list[Decimal] = []
    for w in weights:
        share = (total_cost * w / total_weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        shares.append(share)

    # Put any rounding penny remainder on the largest-weight SKU line so sum(shares) == total_cost.
    remainder = (total_cost - sum(shares, Decimal("0.00"))).quantize(Decimal("0.01"))
    if remainder != Decimal("0.00"):
        largest_idx = max(range(len(weights)), key=lambda i: weights[i])
        shares[largest_idx] = (shares[largest_idx] + remainder).quantize(Decimal("0.01"))

    allocations: list[dict[str, Any]] = []
    for idx, ln in enumerate(sku_lines):
        qty = Decimal(str(ln.get("quantity") or "1"))
        unit_cost = Decimal(str(ln.get("unit_cost") or "0")).quantize(Decimal("0.01"))
        allocated = shares[idx]
        landed_per_unit = (allocated / qty).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        new_unit_cost = (unit_cost + landed_per_unit).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_EVEN
        )
        allocations.append(
            {
                "sku": str(ln.get("sku") or f"SKU-{idx + 1}"),
                "quantity": str(qty),
                "basis_weight": str(weights[idx].quantize(Decimal("0.0001"))),
                "share_pct": str(
                    (weights[idx] / total_weight * Decimal("100")).quantize(Decimal("0.01"))
                ),
                "allocated_landed_cost": str(allocated),
                "original_unit_cost": str(unit_cost),
                "landed_cost_per_unit": str(landed_per_unit),
                "new_landed_unit_cost": str(new_unit_cost),
            }
        )

    return {
        "allocation_basis": basis,
        "total_landed_cost": str(total_cost),
        "allocated_sum": str(sum(shares, Decimal("0.00")).quantize(Decimal("0.01"))),
        "exact_tie_out": sum(shares, Decimal("0.00")) == total_cost,
        "allocations": allocations,
    }


def save_landed_cost_allocation(
    db: Session,
    business_id: UUID,
    *,
    result: dict[str, Any],
    source_bill_id: UUID | None = None,
    po_id: UUID | None = None,
) -> dict[str, Any]:
    aid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.landed_cost_allocations
                (id, business_id, source_bill_id, po_id, allocation_basis,
                 total_landed_cost, allocations)
            VALUES (:id, :bid, :sbid, :poid, :basis, :tot, CAST(:alloc AS jsonb))
            """
        ),
        {
            "id": aid,
            "bid": business_id,
            "sbid": source_bill_id,
            "poid": po_id,
            "basis": result["allocation_basis"],
            "tot": Decimal(result["total_landed_cost"]),
            "alloc": json.dumps(result["allocations"]),
        },
    )
    return {"id": str(aid), **result}
