"""Exact decimal strings and bounded accounting requests."""

import re
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ledgerpilot.shared.schemas import Schema


class AccountCreate(Schema):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["asset", "liability", "equity", "income", "expense"]
    control: Literal["ar", "ap", "loan_receivable", "loan_payable", "tax", "clearing"] | None = None

    @model_validator(mode="after")
    def compatible_control(self) -> "AccountCreate":
        kinds = {
            "ar": {"asset"},
            "ap": {"liability"},
            "loan_receivable": {"asset"},
            "loan_payable": {"liability"},
            "tax": {"asset", "liability"},
            "clearing": {"asset"},
        }
        if self.control and self.kind not in kinds[self.control]:
            raise ValueError("Control account kind is incompatible")
        return self


class AccountUpdate(Schema):
    active: bool
    expected_version: int = Field(ge=0)


class PeriodCreate(Schema):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def ordered(self) -> "PeriodCreate":
        if self.end_date < self.start_date:
            raise ValueError("Period dates are not ordered")
        return self


class PeriodUpdate(Schema):
    locked: bool
    expected_version: int = Field(ge=0)


class LineCreate(Schema):
    account_id: UUID
    debit: str = "0"
    credit: str = "0"

    @field_validator("debit", "credit")
    @classmethod
    def money(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,6})?", value):
            raise ValueError("Use an exact decimal string within NUMERIC(24,6)")
        return format(Decimal(value).normalize(), "f")

    @model_validator(mode="after")
    def one_side(self) -> "LineCreate":
        if (Decimal(self.debit) > 0) == (Decimal(self.credit) > 0):
            raise ValueError("Exactly one of debit and credit must be positive")
        return self


class JournalCreate(Schema):
    accounting_date: date
    memo: str = Field(default="", max_length=2000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_version: int = Field(ge=0)
    lines: list[LineCreate] = Field(min_length=2, max_length=200)


class ReversalCreate(Schema):
    accounting_date: date
    memo: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_version: int = Field(ge=0)


class DraftCreate(Schema):
    accounting_date: date
    memo: str = Field(default="", max_length=2000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    lines: list[LineCreate] = Field(min_length=1, max_length=200)


class DraftUpdate(DraftCreate):
    expected_version: int = Field(ge=0)


class DraftPublish(Schema):
    expected_version: int = Field(ge=0)
    expected_business_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
