"""Frontend-facing read contracts with exact monetary strings."""

from datetime import date
from uuid import UUID

from ledgerpilot.shared.responses import Identifier, Page
from ledgerpilot.trade.schemas import AllocationCreate, ScheduleLine, SourceLine


class PartyRead(Identifier):
    name: str
    email: str | None
    roles: list[str]
    active: bool
    version: int


class PostedRead(Identifier):
    journal_id: UUID


class IssuedRead(PostedRead):
    total: str


class DocumentRead(Identifier):
    kind: str
    number: str
    party_id: UUID
    accounting_date: date
    opening_source_date: date | None
    due_date: date
    service_start: date | None
    service_end: date | None
    control_account_id: UUID
    journal_id: UUID
    total: str
    tax: str
    lines: list[SourceLine]
    original_id: UUID | None
    disputed: bool
    version: int
    outstanding: str | None


class DocumentPage(Page[DocumentRead]):
    as_of: date
    currency: str
    timezone: str
    basis: str


class PaymentRead(PostedRead):
    kind: str
    party_id: UUID
    accounting_date: date
    bank_account_id: UUID
    control_account_id: UUID
    total: str
    fee_amount: str
    fee_account_id: UUID | None
    allocations: list[AllocationCreate]
    original_payment_id: UUID | None


class LoanRead(Identifier):
    name: str
    party_id: UUID
    direction: str
    control_account_id: UUID
    schedule: list[ScheduleLine]
    principal: str
