"""Schemas for queued report exports and balance summaries."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ReportExportRequest(BaseModel):
    report_type: Literal[
        "profit-loss", "balance-sheet", "aging", "controls", "dashboard", "tax-support", "journals"
    ]
    format: Literal["json", "csv"] = "json"
    start: date | None = None
    end: date | None = None
    as_of: date | None = None
    basis: Literal["cash", "accrual"] = "accrual"
    kind: Literal["invoice", "bill"] = "invoice"
    bank_ids: list[UUID] = Field(default_factory=list)


class ReportExportResponse(BaseModel):
    job_id: UUID
    status: str
    status_url: str
    download_url: str
