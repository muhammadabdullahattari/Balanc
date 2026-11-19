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
        portion = base / entry.months
        # Differences between rounded cumulative balances allocate every final cent.
        amount = (portion * entry.installment).quantize(unit, ROUND_HALF_EVEN) - (
            portion * (entry.installment - 1)
        ).quantize(unit, ROUND_HALF_EVEN)
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
