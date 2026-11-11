"""Reviewed, bounded CSV imports compile to the same public accounting workflows.

No missing source dates, counterparties, categories or historical allocations are
invented. GL control accounts are accepted only when final subledgers tie out.
"""

from datetime import date
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.schemas import Schema


class ImportPreview(Schema):
    mode: Literal["chart", "journal", "trade", "opening"]
    content: str = Field(min_length=1, max_length=200000)
    columns: dict[str, str] = Field(default_factory=dict, max_length=20)
    cutover_date: date
    expected_trial: dict[str, str] = Field(default_factory=dict, max_length=1000)
    opening_equity_account_code: str | None = Field(default=None, min_length=1, max_length=40)
    review_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_serializer(mode="wrap")
    def stable_existing_contract(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        # Adding optional opening fields must not invalidate existing import hashes/retries.
        for key in ("opening_equity_account_code", "review_reason"):
            if result.get(key) is None:
                result.pop(key, None)
        return result


class ImportApply(ImportPreview, ops.Command):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ParallelTrialBalance(Schema):
    as_of: date
    expected_trial: dict[str, str] = Field(min_length=1, max_length=1000)


class ImportJournal(ops.Command):
    accounting_date: date
    memo: str = Field(max_length=2000)
    lines: list[LineCreate] = Field(min_length=2, max_length=200)
