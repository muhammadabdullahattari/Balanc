"""Explicit read contracts prevent accidental persistence-field exposure."""

from datetime import date as Date
from datetime import datetime
from uuid import UUID

from ledgerpilot.shared.schemas import Schema


class Page[T](Schema):
    items: list[T]
    next_cursor: str | None


class Identifier(Schema):
    id: UUID


class BusinessSummary(Identifier):
    name: str
    currency: str
    basis: str
    version: int


class BusinessRead(BusinessSummary):
    currency_precision: int
    timezone: str
    policy_version: int
    fiscal_month: int
    jurisdiction: str


class AccountRead(Identifier):
    code: str
    name: str
    kind: str
    control: str | None
    active: bool
    version: int


class PeriodRead(Identifier):
    start_date: Date
    end_date: Date
    locked: bool
    version: int
    status: str
    locked_by: UUID | None


class JournalLineRead(Schema):
    account_id: UUID
    debit: str
    credit: str
    position: int


class JournalRead(Identifier):
    accounting_date: Date
    memo: str
    currency: str
    reversal_of: UUID | None
    reversed_by: UUID | None
    lines: list[JournalLineRead]
    status: str
    version: int
    operation_key: UUID
    period_id: UUID | None
    policy_id: UUID | None
    source_kind: str
    source_id: UUID | None
    approved_by: UUID | None
    posted_at: datetime | None
    book: str


class TrialBalanceRow(Schema):
    account_id: UUID
    code: str
    name: str
    kind: str
    debit: str
    credit: str
    balance: str
    movement_debit: str
    movement_credit: str


class TrialBalanceRead(Schema):
    as_of: Date
    currency: str
    timezone: str
    policy_version: int
    report_type: str
    items: list[TrialBalanceRow]
    total_debit: str
    total_credit: str
    balanced: bool


class ValidationField(Schema):
    location: list[str | int]
    type: str


class ErrorDetail(Schema):
    code: str
    message: str
    fields: list[ValidationField] | None = None


class ErrorEnvelope(Schema):
    error: ErrorDetail
    request_id: UUID | None = None


class ReadinessResponse(Schema):
    status: str
