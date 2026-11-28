"""Day-1 automated Month-over-Month & Year-over-Year flux analysis (Phase K3 / Module 9)."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from ledgerpilot.config import Settings

DEFAULT_PCT_THRESHOLD = Decimal("10.00")
DEFAULT_ABS_THRESHOLD = Decimal("1000.00")


def compute_flux_variances(
    current_balances: list[dict[str, Any]],
    prior_month_balances: list[dict[str, Any]],
    prior_year_balances: list[dict[str, Any]] | None = None,
    *,
    pct_threshold: Decimal = DEFAULT_PCT_THRESHOLD,
    abs_threshold: Decimal = DEFAULT_ABS_THRESHOLD,
) -> list[dict[str, Any]]:
    """Compute MoM and YoY variances and flag material accounts (|pct| >= 10% or |abs| >= 1000)."""
    pm_map = {str(r["account_code"]): Decimal(str(r["balance"])) for r in prior_month_balances}
    py_map = {
        str(r["account_code"]): Decimal(str(r["balance"])) for r in (prior_year_balances or [])
    }

    variances: list[dict[str, Any]] = []
    for row in current_balances:
        code = str(row["account_code"])
        name = str(row["account_name"])
        kind = str(row.get("account_type") or "expense")
        cur = Decimal(str(row["balance"])).quantize(Decimal("0.01"))
        pm = pm_map.get(code, Decimal("0.00")).quantize(Decimal("0.01"))
        py = py_map.get(code, Decimal("0.00")).quantize(Decimal("0.01"))

        mom_abs = (cur - pm).quantize(Decimal("0.01"))
        if pm != Decimal("0.00"):
            mom_pct = ((mom_abs / abs(pm)) * Decimal("100")).quantize(Decimal("0.01"))
        elif cur != Decimal("0.00"):
            mom_pct = Decimal("100.00") if cur > 0 else Decimal("-100.00")
        else:
            mom_pct = Decimal("0.00")

        yoy_abs = (cur - py).quantize(Decimal("0.01"))
        if py != Decimal("0.00"):
            yoy_pct = ((yoy_abs / abs(py)) * Decimal("100")).quantize(Decimal("0.01"))
        elif cur != Decimal("0.00") and prior_year_balances is not None:
            yoy_pct = Decimal("100.00") if cur > 0 else Decimal("-100.00")
        else:
            yoy_pct = Decimal("0.00")

        material = abs(mom_pct) >= pct_threshold or abs(mom_abs) >= abs_threshold
        direction = "increased" if mom_abs > 0 else "decreased" if mom_abs < 0 else "unchanged"
        variances.append(
            {
                "account_code": code,
                "account_name": name,
                "account_type": kind,
                "current_balance": str(cur),
                "prior_month_balance": str(pm),
                "prior_year_balance": str(py),
                "mom_delta_abs": str(mom_abs),
                "mom_delta_pct": str(mom_pct),
                "yoy_delta_abs": str(yoy_abs),
                "yoy_delta_pct": str(yoy_pct),
                "material": material,
                "direction": direction,
            }
        )
    variances.sort(key=lambda v: abs(Decimal(v["mom_delta_abs"])), reverse=True)
    return variances


def _extract_numeric_tokens(text_val: str) -> set[str]:
    raw_nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", text_val)
    normalized: set[str] = set()
    for n in raw_nums:
        clean = n.replace(",", "")
        try:
            d = Decimal(clean)
            normalized.add(str(d.quantize(Decimal("0.01"))))
            normalized.add(str(abs(d).quantize(Decimal("0.01"))))
            if d == d.to_integral_value():
                normalized.add(str(int(abs(d))))
        except Exception:
            continue
    return normalized


def verify_narrative_numbers(narrative: str, variances: list[dict[str, Any]]) -> bool:
    """Reject LLM flux narratives that invent numbers not grounded in the variance table."""
    allowed: set[str] = set()
    for v in variances:
        for key in (
            "current_balance",
            "prior_month_balance",
            "prior_year_balance",
            "mom_delta_abs",
            "mom_delta_pct",
            "yoy_delta_abs",
            "yoy_delta_pct",
        ):
            val = str(v.get(key) or "")
            allowed |= _extract_numeric_tokens(val)
        if v.get("account_code"):
            allowed.add(str(v["account_code"]))
    # Allow standard threshold / date numbers (10, 1000, 1..31, years 2020..2035)
    allowed |= {str(i) for i in range(0, 32)}
    allowed |= {str(y) for y in range(2020, 2036)}
    allowed |= {"10.00", "1000.00", "1000"}
    cited = _extract_numeric_tokens(narrative)
    return cited.issubset(allowed)


def build_deterministic_flux_narrative(
    period_label: str,
    variances: list[dict[str, Any]],
) -> str:
    """Build a concise, audit-grounded flux summary with zero hallucinated numbers."""
    material = [v for v in variances if v["material"]]
    if not material:
        return (
            f"For {period_label}, no accounts exceeded the materiality threshold "
            f"(10.00% or 1,000.00 month-over-month)."
        )
    top = material[:5]
    parts = [f"Day-1 flux summary for {period_label}: {len(material)} material variances detected."]
    for item in top:
        parts.append(
            f"{item['account_name']} ({item['account_code']}) {item['direction']} by "
            f"{item['mom_delta_abs']} ({item['mom_delta_pct']}%) from "
            f"{item['prior_month_balance']} to {item['current_balance']}."
        )
    return " ".join(parts)


def generate_flux_report(
    *,
    business_id: UUID,
    period_start: date,
    period_end: date,
    current_balances: list[dict[str, Any]],
    prior_month_balances: list[dict[str, Any]],
    prior_year_balances: list[dict[str, Any]] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    variances = compute_flux_variances(current_balances, prior_month_balances, prior_year_balances)
    period_label = f"{period_start.isoformat()} to {period_end.isoformat()}"
    narrative = build_deterministic_flux_narrative(period_label, variances)
    narrative_source = "deterministic"

    if settings is not None and getattr(settings, "groq_api_key", None):
        try:
            import importlib

            ai_models = importlib.import_module("ledgerpilot.ai.models")
            adapter = ai_models.GroqModelAdapter(settings)
            candidate = adapter.complete(
                system=(
                    "You are a CFO financial controller. Summarize the material variances below "
                    "using ONLY exact numbers present in the input JSON."
                ),
                user=(
                    f"Period: {period_label}\n"
                    f"Material variances: {[v for v in variances if v['material']][:8]}"
                ),
            ).strip()
            if candidate and verify_narrative_numbers(candidate, variances):
                narrative = candidate
                narrative_source = "llm_verified"
        except Exception:
            pass

    return {
        "business_id": str(business_id),
        "kind": "flux_analysis",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "material_count": sum(1 for v in variances if v["material"]),
        "total_accounts": len(variances),
        "narrative_source": narrative_source,
        "narrative": narrative,
        "variances": variances,
    }
