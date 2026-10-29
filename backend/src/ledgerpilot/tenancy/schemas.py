"""Validated business settings and invitation contracts."""

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from ledgerpilot.shared.schemas import Schema

# Explicit supported pilot currencies. Foreign-currency journals remain disabled.
CURRENCIES = {
    "USD": 2,
    "PKR": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "KWD": 3,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
}


class BusinessCreate(Schema):
    name: str = Field(min_length=1, max_length=120)
    currency: str
    timezone: str
    basis: Literal["cash", "accrual"]
    fiscal_month: int = Field(default=1, ge=1, le=12)
    jurisdiction: str = Field(min_length=1, max_length=100)

    @field_validator("currency")
    @classmethod
    def currency_supported(cls, value: str) -> str:
        if value not in CURRENCIES:
            raise ValueError("Currency is not supported in this pilot")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_supported(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Use an IANA timezone") from None
        return value


class InviteCreate(Schema):
    email: str = Field(max_length=254)
    role: Literal["owner", "accountant", "bookkeeper", "reviewer", "auditor"]


class PolicyChange(Schema):
    basis: Literal["cash", "accrual"]
    jurisdiction: str = Field(min_length=1, max_length=100)
    effective_date: date
    expected_version: int = Field(ge=0)
