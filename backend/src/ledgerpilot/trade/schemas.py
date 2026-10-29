"""Bounded source transaction requests; monetary values are decimal strings."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.shared.operations import Command
from ledgerpilot.shared.schemas import Schema


def exact_amount(value: str) -> str:
    return LineCreate.money(value)


Amount = str


class PartyCreate(Schema):
    name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=254)
    roles: list[Literal["customer", "supplier", "lender", "borrower"]] = Field(
        min_length=1,
        max_length=4,
    )

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, values: list[str]) -> list[str]:
        return sorted(set(values))


class PartyUpdate(PartyCreate):
    expected_version: int = Field(ge=0)
    active: bool = True


class SourceLine(Schema):
    account_id: UUID
    description: str = Field(min_length=1, max_length=300)
    amount: str
    tax_amount: str = "0"
    tax_account_id: UUID | None = None
    tax_code: str | None = Field(default=None, max_length=40)

    _amount = field_validator("amount", "tax_amount")(exact_amount)


class DocumentCreate(Command):
    kind: Literal["invoice", "bill", "sales_receipt", "customer_credit", "vendor_credit"]
    number: str = Field(min_length=1, max_length=80)
    party_id: UUID
    accounting_date: date
    due_date: date
    service_start: date | None = None
    service_end: date | None = None
    control_account_id: UUID
    bank_account_id: UUID | None = None
    original_id: UUID | None = None
    lines: list[SourceLine] = Field(min_length=1, max_length=90)

    @model_validator(mode="after")
    def dates(self) -> "DocumentCreate":
        if self.due_date < self.accounting_date:
            raise ValueError("Due date precedes document date")
        if (self.service_start is None) != (self.service_end is None):
            raise ValueError("Both service dates are required together")
        if self.service_start and self.service_end and self.service_start > self.service_end:
            raise ValueError("Service dates are not ordered")
        if self.kind == "sales_receipt" and not self.bank_account_id:
            raise ValueError("Sales receipts need an approved bank account")
        if (self.kind in {"customer_credit", "vendor_credit"}) != (self.original_id is not None):
            raise ValueError("Credit notes require an original invoice or bill")
        return self


class AllocationCreate(Schema):
    document_id: UUID
    credit_note_id: UUID | None = None
    amount: str
    _amount = field_validator("amount")(exact_amount)


class PaymentCreate(Command):
    kind: Literal["receipt", "payment", "customer_refund", "vendor_refund"]
    party_id: UUID
    accounting_date: date
    bank_account_id: UUID
    control_account_id: UUID
    amount: str
    fee_amount: str = "0"
    fee_account_id: UUID | None = None
    allocations: list[AllocationCreate] = Field(default_factory=list, max_length=90)
    original_payment_id: UUID | None = None
    _amount = field_validator("amount", "fee_amount")(exact_amount)


class DisputeUpdate(Schema):
    disputed: bool
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class ScheduleLine(Schema):
    due_date: date
    principal: str
    interest: str = "0"
    _amount = field_validator("principal", "interest")(exact_amount)


class LoanCreate(Schema):
    party_id: UUID
    direction: Literal["payable", "receivable"]
    control_account_id: UUID
    name: str = Field(min_length=1, max_length=160)
    schedule: list[ScheduleLine] = Field(default_factory=list, max_length=120)


class LoanMovementCreate(Command):
    kind: Literal["drawdown", "repayment"]
    accounting_date: date
    bank_account_id: UUID
    principal: str
    interest: str = "0"
    fees: str = "0"
    interest_account_id: UUID | None = None
    fee_account_id: UUID | None = None
    _amount = field_validator("principal", "interest", "fees")(exact_amount)
