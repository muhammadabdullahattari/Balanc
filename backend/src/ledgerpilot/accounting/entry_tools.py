"""Build exact functional-currency journal proposals without posting them."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from ledgerpilot.accounting.schemas import DraftCreate, LineCreate
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema

Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class EntryInputs(Schema):
    reference: str = Field(min_length=1, max_length=160)
    accounting_date: date
    debit_account: UUID
    credit_account: UUID
    amount: Money


class Accrual(EntryInputs):
    kind: Literal["accrual"]


class Depreciation(EntryInputs):
    kind: Literal["depreciation"]
    residual: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    months: int = Field(ge=1, le=1200)
    installment: int = Field(ge=1, le=1200)


class Conversion(EntryInputs):
    kind: Literal["fx"]
    source_currency: Literal["USD", "GBP", "PKR", "EUR", "AED"]
    rate: Decimal = Field(gt=0, max_digits=16, decimal_places=8)
    rate_source: str = Field(min_length=1, max_length=120)


Entry = Annotated[Accrual | Depreciation | Conversion, Field(discriminator="kind")]


def straight_line_installment(base: Decimal, months: int, index: int, unit: Decimal) -> Decimal:
    """Difference of rounded cumulative amounts allocates the residual cent."""
    portion = base / months
    return (portion * index).quantize(unit, ROUND_HALF_EVEN) - (
        portion * (index - 1)
    ).quantize(unit, ROUND_HALF_EVEN)


def journal_preview(entry: Entry, currency: str, precision: int) -> DraftCreate:
    unit = Decimal(1).scaleb(-precision)
    amount = entry.amount
    memo = f"{entry.kind.title()} — {entry.reference}"
    if entry.credit_account == entry.debit_account:
        raise DomainError("ERR_ACCOUNT", "Choose two different accounts.", 422)
    if isinstance(entry, Depreciation):
        if entry.residual >= amount or entry.installment > entry.months:
            raise DomainError(
                "ERR_SCHEDULE", "Check the residual value and installment number.", 422
            )
        base = amount - entry.residual
        if base != base.quantize(unit):
            raise DomainError("ERR_PRECISION", "Use the business currency precision.", 422)
        amount = straight_line_installment(base, entry.months, entry.installment, unit)
        memo += (
            f"; straight-line {entry.installment}/{entry.months}; cost {entry.amount}; "
            f"residual {entry.residual}"
        )
    elif isinstance(entry, Conversion):
        if entry.source_currency == currency:
            raise DomainError("ERR_CURRENCY", "Choose a different transaction currency.", 422)
        converted = amount * entry.rate
        if converted >= 10**18:
            raise DomainError("ERR_AMOUNT", "Converted amount exceeds ledger limits.", 422)
        amount = converted.quantize(unit, ROUND_HALF_EVEN)
        memo += (
            f"; {entry.amount} {entry.source_currency} at {entry.rate} "
            f"{currency}/{entry.source_currency}; source {entry.rate_source}"
        )
    if amount <= 0 or amount != amount.quantize(unit) or amount >= 10**18:
        raise DomainError(
            "ERR_AMOUNT",
            "The calculated amount must fit the ledger precision and be positive.",
            422,
        )
    value = format(amount, "f")
    return DraftCreate(
        accounting_date=entry.accounting_date,
        memo=memo,
        currency=currency,
        lines=[
            LineCreate(account_id=entry.debit_account, debit=value),
            LineCreate(account_id=entry.credit_account, credit=value),
        ],
    )


def revaluation_delta(
    foreign_units: Decimal, closing_rate: Decimal, carrying_value: Decimal, precision: int
) -> Decimal:
    """Signed functional-currency adjustment; debit balances are positive.

    A zero foreign balance may still have a residual carrying value. Round the
    target carrying value before subtracting so repeat valuation has zero delta.
    """
    if not all(value.is_finite() for value in (foreign_units, closing_rate, carrying_value)):
        raise DomainError("ERR_AMOUNT", "Use finite amounts and rates.", 422)
    if closing_rate <= 0 or precision not in range(7):
        raise DomainError("ERR_CURRENCY", "Check the closing rate and currency precision.", 422)
    unit = Decimal(1).scaleb(-precision)
    if carrying_value != carrying_value.quantize(unit):
        raise DomainError("ERR_PRECISION", "Check the carrying balance precision.", 422)
    return (foreign_units * closing_rate).quantize(unit, ROUND_HALF_EVEN) - carrying_value
