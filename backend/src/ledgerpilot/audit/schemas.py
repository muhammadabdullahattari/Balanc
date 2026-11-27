"""Pydantic HTTP request and response schemas for LangGraph financial audit endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from ledgerpilot.shared.schemas import Schema

ManualAuditMode = Literal["monthly", "yearly", "emergency"]
AnyAuditMode = Literal[
    "monthly",
    "yearly",
    "emergency",
    "auto_balance_sheet",
    "auto_period_close",
]


class AuditRunCreateRequest(Schema):
    """Request payload for running a monthly, yearly, or emergency financial audit."""

    audit_type: ManualAuditMode
    month: str | None = Field(
        default=None,
        max_length=7,
        description="Target month in YYYY-MM format when audit_type='monthly'.",
    )
    year: int | None = Field(
        default=None,
        ge=1900,
        le=2200,
        description="Target fiscal or calendar year when audit_type='yearly'.",
    )
    calendar_year: bool = Field(
        default=False,
        description="Use Jan 1..Dec 31 instead of workspace fiscal_month for yearly audits.",
    )
    start_date: date | None = Field(
        default=None,
        description=(
            "Optional start date for emergency/on-demand audits (defaults to earliest record)."
        ),
    )
    end_date: date | None = Field(
        default=None,
        description="Optional end date for emergency/on-demand audits (defaults to today).",
    )
    run_async: bool = Field(
        default=False,
        description="Queue the audit as a durable background job instead of waiting inline.",
    )

    @model_validator(mode="after")
    def check_mode_fields(self) -> AuditRunCreateRequest:
        if self.audit_type == "monthly" and not self.month:
            raise ValueError("Provide 'month' (YYYY-MM) for a monthly audit.")
        if self.audit_type == "yearly" and self.year is None:
            raise ValueError("Provide 'year' (YYYY) for a yearly audit.")
        if (
            self.audit_type == "emergency"
            and self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date.")
        return self


class AuditFindingResponse(Schema):
    id: str
    severity: Literal["Critical", "High", "Medium", "Low"]
    category: str
    rule_violated: str
    affected_records: list[str]
    expected_vs_actual: dict[str, str]
    evidence: list[str]
    recommendation: str


class FindingNarrativeItem(Schema):
    id: str
    severity: str
    what: str
    where: str
    why_it_matters: str
    recommended_fix: str


class PolishedReportResponse(Schema):
    executive_summary: str
    severity_breakdown: str
    findings_narrative: list[FindingNarrativeItem]
    next_steps: list[str]
    markdown: str
    provider: str
    model_name: str
    fallback_used: bool
    fallback_reason: str = ""
    numeric_guard_passed: bool = True
    chunks_processed: int = 1


class SeveritySummaryResponse(Schema):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0
    health_status: str = "clean_verified"


class AuditRunDetailResponse(Schema):
    id: UUID
    business_id: UUID
    audit_type: AnyAuditMode
    trigger_source: str
    status: str
    thread_id: str
    job_id: UUID | None = None
    period_start: date
    period_end: date
    scope_filters: dict[str, Any]
    loaded_data_summary: dict[str, Any]
    severity_summary: SeveritySummaryResponse
    findings: list[AuditFindingResponse]
    raw_report: dict[str, Any]
    polished_report: PolishedReportResponse
    retrieved_context: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_by: UUID
    created_at: datetime


class AuditRunSummaryItem(Schema):
    id: UUID
    audit_type: AnyAuditMode
    trigger_source: str
    status: str
    job_id: UUID | None = None
    period_start: date
    period_end: date
    severity_summary: SeveritySummaryResponse
    executive_summary: str
    created_at: datetime


class AuditRunListResponse(Schema):
    items: list[AuditRunSummaryItem]
    next_cursor: str | None = None


class AuditGraphTopologyResponse(Schema):
    mermaid: str
    nodes: list[str]
    tools: list[str]
