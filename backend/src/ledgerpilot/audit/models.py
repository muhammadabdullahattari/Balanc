"""Append-only audit events and persisted LangGraph financial audit runs."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "app"}
    # Some roles may append their own events without reading the workspace audit log.
    __mapper_args__ = {"eager_defaults": False}
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), index=True)
    business_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class FinancialAuditRun(Base):
    __tablename__ = "financial_audit_runs"
    __table_args__ = (
        CheckConstraint("period_end >= period_start"),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    audit_type: Mapped[str] = mapped_column(String(32))
    trigger_source: Mapped[str] = mapped_column(String(40), default="manual")
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    scope_filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.background_jobs.id"), nullable=True)
    thread_id: Mapped[str] = mapped_column(String(120))
    loaded_data_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    severity_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    raw_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    polished_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retrieved_context: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
