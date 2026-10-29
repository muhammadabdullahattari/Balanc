"""Bank review read contracts omit immutable raw bytes and private job state."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from ledgerpilot.banking.schemas import CSVMapping
from ledgerpilot.shared.responses import Identifier


class BankImportRead(Identifier):
    account_id: UUID
    file_hash: str
    mapping: CSVMapping
    row_count: int
    created_at: datetime


class BankImportResult(Identifier):
    reimport: bool
    created: int


class BankRowRead(Identifier):
    account_id: UUID
    import_id: UUID
    accounting_date: date
    description: str
    amount: str
    state: str
    version: int
    journal_id: UUID | None
    reason: str | None
    peer_id: UUID | None
    external_id: str | None


class DispositionRead(Identifier):
    disposition: str
    state: str
    version: int
    journal_id: UUID | None


class RuleRead(Identifier):
    name: str
    contains: str
    account_id: UUID
    priority: int
    version: int
    active: bool
    history: list[dict[str, Any]]
