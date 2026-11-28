"""Fixed Asset CapEx Scanner & IRS Pub 946 MACRS / Section 179 Dual Schedule (Phase N2)."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from ledgerpilot.shared.errors import DomainError

# IRS Publication 946 Table A-1 (GDS Half-Year Convention percentages, exact Decimal)
MACRS_HY_TABLES: dict[int, list[Decimal]] = {
    3: [Decimal("33.33"), Decimal("44.45"), Decimal("14.81"), Decimal("7.41")],
    5: [
        Decimal("20.00"),
        Decimal("32.00"),
        Decimal("19.20"),
        Decimal("11.52"),
        Decimal("11.52"),
        Decimal("5.76"),
    ],
    7: [
        Decimal("14.29"),
        Decimal("24.49"),
        Decimal("17.49"),
        Decimal("12.49"),
        Decimal("8.93"),
        Decimal("8.92"),
        Decimal("8.93"),
        Decimal("4.46"),
    ],
    10: [
        Decimal("10.00"),
        Decimal("18.00"),
        Decimal("14.40"),
        Decimal("11.52"),
        Decimal("9.22"),
        Decimal("7.37"),
        Decimal("6.55"),
        Decimal("6.55"),
        Decimal("6.56"),
        Decimal("6.55"),
        Decimal("3.28"),
    ],
    15: [
        Decimal("5.00"),
        Decimal("9.50"),
        Decimal("8.55"),
        Decimal("7.70"),
        Decimal("6.93"),
        Decimal("6.23"),
        Decimal("5.90"),
        Decimal("5.90"),
        Decimal("5.91"),
        Decimal("5.90"),
        Decimal("5.91"),
        Decimal("5.90"),
        Decimal("5.91"),
        Decimal("5.90"),
        Decimal("5.91"),
        Decimal("2.95"),
    ],
    20: [
        Decimal("3.750"),
        Decimal("7.219"),
        Decimal("6.677"),
        Decimal("6.177"),
        Decimal("5.713"),
        Decimal("5.285"),
        Decimal("4.888"),
        Decimal("4.522"),
        Decimal("4.462"),
        Decimal("4.461"),
        Decimal("4.462"),
        Decimal("4.461"),
        Decimal("4.462"),
        Decimal("4.461"),
        Decimal("4.462"),
        Decimal("4.461"),
        Decimal("4.462"),
        Decimal("4.461"),
        Decimal("4.462"),
        Decimal("4.461"),
        Decimal("2.231"),
    ],
}


def scan_capitalization_candidates(
    expense_lines: list[dict[str, Any]],
    *,
    threshold: Decimal = Decimal("2500.00"),
) -> list[dict[str, Any]]:
    """Flag expense lines above the capitalization threshold (default $2,500)."""
    thresh = Decimal(str(threshold)).quantize(Decimal("0.01"))
    flagged: list[dict[str, Any]] = []
    for ln in expense_lines:
        amt = abs(Decimal(str(ln.get("amount") or "0"))).quantize(Decimal("0.01"))
        if amt >= thresh:
            desc = str(ln.get("description") or "")
            suggested_period = (
                5
                if any(
                    k in desc.lower()
                    for k in ("laptop", "macbook", "server", "computer", "vehicle", "truck", "car")
                )
                else 7
            )
            flagged.append(
                {
                    "line_id": str(ln.get("id") or ""),
                    "description": desc,
                    "amount": str(amt),
                    "threshold": str(thresh),
                    "suggested_recovery_period_years": suggested_period,
                    "suggested_tax_method": "MACRS_GDS_200DB",
                    "reason": f"Expense {amt} meets or exceeds capitalization threshold {thresh}.",
                }
            )
    return flagged


def compute_dual_depreciation_schedule(
    *,
    asset_name: str,
    cost: Decimal,
    salvage_value: Decimal = Decimal("0.00"),
    gaap_useful_life_years: int = 5,
    macrs_recovery_period: int = 5,
    section_179_amount: Decimal = Decimal("0.00"),
    convention: str = "half_year",
) -> dict[str, Any]:
    """Compute parallel GAAP straight-line and IRS Pub 946 MACRS + Section 179 schedules."""
    total_cost = Decimal(str(cost)).quantize(Decimal("0.01"))
    salvage = Decimal(str(salvage_value)).quantize(Decimal("0.01"))
    sec179 = Decimal(str(section_179_amount)).quantize(Decimal("0.01"))
    if total_cost <= Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Asset cost must be positive.", 422)
    if salvage < Decimal("0.00") or salvage > total_cost:
        raise DomainError("ERR_VALIDATION", "Salvage value must be between 0 and cost.", 422)
    if sec179 < Decimal("0.00") or sec179 > total_cost:
        raise DomainError("ERR_VALIDATION", "Section 179 deduction cannot exceed asset cost.", 422)
    if macrs_recovery_period not in MACRS_HY_TABLES:
        raise DomainError(
            "ERR_VALIDATION",
            f"MACRS recovery period must be one of {sorted(MACRS_HY_TABLES.keys())}.",
            422,
        )
    if gaap_useful_life_years < 1 or gaap_useful_life_years > 40:
        raise DomainError("ERR_VALIDATION", "GAAP useful life must be between 1 and 40 years.", 422)

    # Tax MACRS basis = Cost - Section 179 (MACRS ignores salvage value per IRS Pub 946)
    macrs_basis = (total_cost - sec179).quantize(Decimal("0.01"))
    rates = MACRS_HY_TABLES[macrs_recovery_period]

    tax_by_year: list[Decimal] = []
    running_tax = Decimal("0.00")
    for idx, pct in enumerate(rates):
        if idx == len(rates) - 1:
            depr = (macrs_basis - running_tax).quantize(Decimal("0.01"))
        else:
            depr = (macrs_basis * pct / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
            running_tax += depr
        if idx == 0:
            depr = (depr + sec179).quantize(Decimal("0.01"))
        tax_by_year.append(depr)

    # GAAP Straight-Line
    gaap_basis = (total_cost - salvage).quantize(Decimal("0.01"))
    gaap_annual = (gaap_basis / Decimal(gaap_useful_life_years)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    gaap_by_year: list[Decimal] = []
    running_gaap = Decimal("0.00")
    for idx in range(gaap_useful_life_years):
        if idx == gaap_useful_life_years - 1:
            g = (gaap_basis - running_gaap).quantize(Decimal("0.01"))
        else:
            g = gaap_annual
            running_gaap += g
        gaap_by_year.append(g)

    horizon = max(len(tax_by_year), len(gaap_by_year))
    years: list[dict[str, str | int]] = []
    cum_gaap = Decimal("0.00")
    cum_tax = Decimal("0.00")
    for y in range(horizon):
        g_amt = gaap_by_year[y] if y < len(gaap_by_year) else Decimal("0.00")
        t_amt = tax_by_year[y] if y < len(tax_by_year) else Decimal("0.00")
        cum_gaap = (cum_gaap + g_amt).quantize(Decimal("0.01"))
        cum_tax = (cum_tax + t_amt).quantize(Decimal("0.01"))
        years.append(
            {
                "year": y + 1,
                "gaap_depreciation": str(g_amt),
                "tax_macrs_depreciation": str(t_amt),
                "book_tax_difference": str((t_amt - g_amt).quantize(Decimal("0.01"))),
                "gaap_nbv": str((total_cost - cum_gaap).quantize(Decimal("0.01"))),
                "tax_basis_remaining": str((total_cost - cum_tax).quantize(Decimal("0.01"))),
            }
        )

    return {
        "asset_name": asset_name,
        "cost": str(total_cost),
        "salvage_value": str(salvage),
        "section_179_amount": str(sec179),
        "macrs_basis": str(macrs_basis),
        "macrs_recovery_period": macrs_recovery_period,
        "convention": convention,
        "gaap_useful_life_years": gaap_useful_life_years,
        "total_gaap_depreciation": str(cum_gaap),
        "total_tax_depreciation": str(cum_tax),
        "schedule": years,
    }
