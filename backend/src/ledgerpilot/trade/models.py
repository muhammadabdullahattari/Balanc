"""Tenant-linked operational subledgers; immutable financial source records."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base


class TenantRecord:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)


class Party(TenantRecord, Base):
    __tablename__ = "parties"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(254))
    roles: Mapped[list[str]] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=0)


class Operation(TenantRecord, Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("business_id", "key"), {"schema": "app"})
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(60))
    journal_id: Mapped[UUID | None]
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TradeDocument(TenantRecord, Base):
    __tablename__ = "trade_documents"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    kind: Mapped[str] = mapped_column(String(30))
    number: Mapped[str] = mapped_column(String(80))
    party_id: Mapped[UUID]
    accounting_date: Mapped[date]
    opening_source_date: Mapped[date | None]
    due_date: Mapped[date]
    service_start: Mapped[date | None]
    service_end: Mapped[date | None]
    control_account_id: Mapped[UUID]
    journal_id: Mapped[UUID]
    total: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    tax: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    original_id: Mapped[UUID | None]
    disputed: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=0)


class SourceDraft(TenantRecord, Base):
    __tablename__ = "source_drafts"
    __table_args__ = {"schema": "app"}
    key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(default=0)
    document_id: Mapped[UUID | None]
    prepared_by: Mapped[UUID]


class Payment(TenantRecord, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    kind: Mapped[str] = mapped_column(String(20))
    party_id: Mapped[UUID]
    accounting_date: Mapped[date]
    bank_account_id: Mapped[UUID]
    control_account_id: Mapped[UUID]
    total: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 6), default=Decimal(0), server_default="0"
    )
    fee_account_id: Mapped[UUID | None]
    journal_id: Mapped[UUID]
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    original_payment_id: Mapped[UUID | None]


class Allocation(TenantRecord, Base):
    __tablename__ = "allocations"
    __table_args__ = {"schema": "app"}
    payment_id: Mapped[UUID]
    document_id: Mapped[UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    accounting_date: Mapped[date]


class Loan(TenantRecord, Base):
    __tablename__ = "loans"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    party_id: Mapped[UUID]
    direction: Mapped[str] = mapped_column(String(10))
    control_account_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(160))
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class LoanMovement(TenantRecord, Base):
    __tablename__ = "loan_movements"
    __table_args__ = {"schema": "app"}
    loan_id: Mapped[UUID]
    accounting_date: Mapped[date]
    kind: Mapped[str] = mapped_column(String(20))
    principal: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    interest: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    journal_id: Mapped[UUID]
