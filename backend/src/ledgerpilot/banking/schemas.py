"""Bank CSV mapping and explicit human review contracts."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.shared.operations import Command
from ledgerpilot.shared.schemas import Schema


def exact_amount(value: str) -> str:
    return LineCreate.money(value)


class CSVMapping(Schema):
    date_column: str = Field(min_length=1, max_length=120)
    amount_column: str = Field(min_length=1, max_length=120)
    description_column: str = Field(min_length=1, max_length=120)
    external_id_column: str | None = Field(default=None, max_length=120)
    date_format: Literal["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"] = "%Y-%m-%d"


class CSVPreview(Schema):
    account_id: UUID
    content: str = Field(min_length=1, max_length=200000)
    mapping: CSVMapping


class CSVImport(CSVPreview):
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Split(Schema):
    account_id: UUID
    amount: str
    _money = field_validator("amount")(exact_amount)


class BankDisposition(Command):
    expected_row_version: int = Field(ge=0)
    action: Literal["categorize", "split", "transfer", "card_payment", "match", "exclude"]
    reason: str = Field(min_length=1, max_length=1000)
    splits: list[Split] = Field(default_factory=list, max_length=90)
    target_account_id: UUID | None = None
    peer_id: UUID | None = None
    journal_id: UUID | None = None
    journal_ids: list[UUID] = Field(default_factory=list, max_length=20)
    allow_clearing: bool = False

    @field_validator("journal_ids")
    @classmethod
    def unique_journals(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("A journal cannot be matched twice in a bundle")
        return sorted(values, key=str)


class RuleCreate(Schema):
    name: str = Field(min_length=1, max_length=160)
    contains: str = Field(min_length=2, max_length=120)
    account_id: UUID
    priority: int = Field(default=100, ge=0, le=1000)


class RuleUpdate(RuleCreate):
    expected_version: int = Field(ge=0)


class RuleActivation(Schema):
    expected_version: int = Field(ge=0)
    active: bool
    preview_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RuleRollback(Schema):
    expected_version: int = Field(ge=0)
    restore_version: int = Field(ge=0)


class OutstandingItem(Schema):
    journal_id: UUID
    position: int = Field(ge=1, le=200)


class StatementCreate(Command):
    account_id: UUID
    as_of: date
    closing_balance: str = Field(pattern=r"^-?[0-9]{1,18}(?:\.[0-9]{1,6})?$")
    outstanding: list[OutstandingItem] = Field(default_factory=list, max_length=500)
    evidence_id: UUID
