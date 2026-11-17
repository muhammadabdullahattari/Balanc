"""Approved disposal policies and durable, tenant-scoped cleanup requests."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base
from ledgerpilot.trade.models import TenantRecord


class CleanupPolicy(TenantRecord, Base):
    __tablename__ = "cleanup_policies"
    __table_args__ = {"schema": "app"}
    record_type: Mapped[str]
    location: Mapped[str]
    retention_days: Mapped[int]
    approval_role: Mapped[str]
    reference: Mapped[str]
    created_by: Mapped[UUID]
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CleanupRequest(TenantRecord, Base):
    __tablename__ = "cleanup_requests"
    __table_args__ = {"schema": "app"}
    policy_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    source_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str]
    created_by: Mapped[UUID]
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(default="preview")
    attempts: Mapped[int] = mapped_column(default=0)
    outcome: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CleanupEvent(TenantRecord, Base):
    __tablename__ = "cleanup_events"
    __table_args__ = {"schema": "app"}
    request_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    action: Mapped[str]
    detail: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DisposalIntent(TenantRecord, Base):
    __tablename__ = "disposal_intents"
    __table_args__ = {"schema": "app"}
    cleanup_request_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    location: Mapped[str]
    object_key: Mapped[str]
    state: Mapped[str] = mapped_column(default="intent_recorded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

