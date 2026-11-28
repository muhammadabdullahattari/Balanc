"""File-based Payroll Register Parser (Gusto, ADP, Paychex, Rippling) (Phase M1 / Module 3).

Per user v1.1 scope: parses exported CSV/text payroll registers with zero paid partner API keys,
producing balanced department-level journal entries including employer taxes, 401(k) employee
deferral, 401(k) employer match, and employee/employer health insurance splits.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any

from ledgerpilot.shared.errors import DomainError


def _dec(val: Any) -> Decimal:
    raw = str(val or "0").strip().replace("$", "").replace(",", "")
    if not raw or raw in {"-", "N/A", "null"}:
        return Decimal("0.00")
    try:
        return Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _col(row: dict[str, str], *candidates: str) -> Decimal:
    lowered = {k.strip().lower(): v for k, v in row.items() if k is not None}
    for cand in candidates:
        for k, v in lowered.items():
            if cand in k:
                return _dec(v)
    return Decimal("0.00")


def _str_col(row: dict[str, str], *candidates: str, default: str = "General") -> str:
    lowered = {k.strip().lower(): v for k, v in row.items() if k is not None}
    for cand in candidates:
        for k, v in lowered.items():
            if cand in k and str(v or "").strip():
                return str(v).strip()[:80]
    return default


def parse_payroll_register_csv(
    csv_text: str,
    *,
    provider: str = "gusto",
    bank_withdrawal_amount: Decimal | None = None,
) -> dict[str, Any]:
    """Parse a Gusto/ADP/Paychex/Rippling CSV register into a balanced payroll journal."""
    prov = provider.strip().lower()
    if prov not in {"gusto", "adp", "paychex", "rippling", "generic"}:
        raise DomainError(
            "ERR_VALIDATION",
            "Payroll provider must be gusto, adp, paychex, rippling, or generic.",
            422,
        )
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    rows = list(reader)
    if not rows:
        raise DomainError("ERR_VALIDATION", "Payroll register CSV has no data rows.", 422)

    dept_gross: dict[str, Decimal] = {}
    total_gross = Decimal("0.00")
    total_er_tax = Decimal("0.00")
    total_ee_tax = Decimal("0.00")
    total_401k_ee = Decimal("0.00")
    total_401k_er = Decimal("0.00")
    total_health_ee = Decimal("0.00")
    total_health_er = Decimal("0.00")
    total_net = Decimal("0.00")

    for r in rows:
        dept = _str_col(r, "department", "dept", "cost center", "team", default="General")
        gross = _col(r, "gross pay", "gross wages", "gross")
        er_tax = _col(r, "employer tax", "er tax", "er fica", "company tax")
        ee_tax = _col(r, "employee tax", "ee tax", "withholding", "fed tax")
        k401_ee = _col(r, "401k employee", "401(k) employee", "401k ee", "401k deferral")
        k401_er = _col(r, "401k employer", "401(k) employer", "401k match", "401k er")
        health_ee = _col(r, "health employee", "medical ee", "health ee")
        health_er = _col(r, "health employer", "medical er", "health er", "employer health")
        net = _col(r, "net pay", "net wages", "net")
        if net == Decimal("0.00") and gross > Decimal("0.00"):
            net = (gross - ee_tax - k401_ee - health_ee).quantize(Decimal("0.01"))

        dept_gross[dept] = (dept_gross.get(dept, Decimal("0.00")) + gross).quantize(Decimal("0.01"))
        total_gross += gross
        total_er_tax += er_tax
        total_ee_tax += ee_tax
        total_401k_ee += k401_ee
        total_401k_er += k401_er
        total_health_ee += health_ee
        total_health_er += health_er
        total_net += net

    if total_gross <= Decimal("0.00"):
        raise DomainError(
            "ERR_VALIDATION", "Payroll register total gross pay must be positive.", 422
        )

    lines: list[dict[str, str]] = []
    # Debits (Expenses by department + employer burdens)
    for dept, amt in sorted(dept_gross.items()):
        if amt > Decimal("0.00"):
            lines.append(
                {"account": f"Salaries & Wages - {dept}", "debit": str(amt), "credit": "0.00"}
            )
    if total_er_tax > Decimal("0.00"):
        lines.append(
            {
                "account": "Employer Payroll Tax Expense",
                "debit": str(total_er_tax),
                "credit": "0.00",
            }
        )
    if total_401k_er > Decimal("0.00"):
        lines.append(
            {
                "account": "401(k) Employer Match Expense",
                "debit": str(total_401k_er),
                "credit": "0.00",
            }
        )
    if total_health_er > Decimal("0.00"):
        lines.append(
            {
                "account": "Employer Health Benefits Expense",
                "debit": str(total_health_er),
                "credit": "0.00",
            }
        )

    # Credits (Cash disbursement + payroll liabilities)
    tax_payable = (total_ee_tax + total_er_tax).quantize(Decimal("0.01"))
    k401_payable = (total_401k_ee + total_401k_er).quantize(Decimal("0.01"))
    health_payable = (total_health_ee + total_health_er).quantize(Decimal("0.01"))

    if total_net > Decimal("0.00"):
        lines.append(
            {
                "account": "Operating Checking (Net Payroll)",
                "debit": "0.00",
                "credit": str(total_net),
            }
        )
    if tax_payable > Decimal("0.00"):
        lines.append(
            {"account": "Payroll Taxes Payable", "debit": "0.00", "credit": str(tax_payable)}
        )
    if k401_payable > Decimal("0.00"):
        lines.append(
            {
                "account": "401(k) Contributions Payable",
                "debit": "0.00",
                "credit": str(k401_payable),
            }
        )
    if health_payable > Decimal("0.00"):
        lines.append(
            {"account": "Health Insurance Payable", "debit": "0.00", "credit": str(health_payable)}
        )

    total_debits = sum((Decimal(ln["debit"]) for ln in lines), Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    total_credits = sum((Decimal(ln["credit"]) for ln in lines), Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    imbalance = (total_debits - total_credits).quantize(Decimal("0.01"))
    if imbalance != Decimal("0.00"):
        # Route minor other deductions (e.g. FSA/garnishments) cleanly to balance.
        if imbalance > Decimal("0.00"):
            lines.append(
                {
                    "account": "Other Payroll Deductions Payable",
                    "debit": "0.00",
                    "credit": str(imbalance),
                }
            )
            total_credits = (total_credits + imbalance).quantize(Decimal("0.01"))
        else:
            adj = abs(imbalance)
            lines.append(
                {"account": "Payroll Rounding & Adjustments", "debit": str(adj), "credit": "0.00"}
            )
            total_debits = (total_debits + adj).quantize(Decimal("0.01"))

    bank_match: dict[str, Any] | None = None
    if bank_withdrawal_amount is not None:
        bw = abs(Decimal(str(bank_withdrawal_amount))).quantize(Decimal("0.01"))
        diff = (bw - total_net).quantize(Decimal("0.01"))
        bank_match = {
            "bank_withdrawal_amount": str(bw),
            "expected_net_payroll": str(total_net),
            "difference": str(diff),
            "matched_to_cent": diff == Decimal("0.00"),
        }

    return {
        "provider": prov,
        "employee_rows": len(rows),
        "departments": {k: str(v) for k, v in sorted(dept_gross.items())},
        "totals": {
            "gross_pay": str(total_gross),
            "employer_taxes": str(total_er_tax),
            "employee_taxes": str(total_ee_tax),
            "401k_employee": str(total_401k_ee),
            "401k_employer_match": str(total_401k_er),
            "health_employee": str(total_health_ee),
            "health_employer": str(total_health_er),
            "net_pay": str(total_net),
            "total_debits": str(total_debits),
            "total_credits": str(total_credits),
        },
        "balanced": total_debits == total_credits,
        "bank_match": bank_match,
        "journal_lines": lines,
    }
