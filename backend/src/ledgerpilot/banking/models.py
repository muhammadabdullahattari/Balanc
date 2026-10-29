"""Immutable bank intake with separately versioned review dispositions."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base
from ledgerpilot.trade.models import TenantRecord


class BankImport(TenantRecord, Base):
    __tablename__ = "bank_imports"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    account_id: Mapped[UUID]
    file_hash: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB)
    row_count: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class BankRow(TenantRecord, Base):
    __tablename__ = "bank_rows"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    import_id: Mapped[UUID]
    account_id: Mapped[UUID]
    position: Mapped[int]
    accounting_date: Mapped[date]
    description: Mapped[str] = mapped_column(String(1000))
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    external_id: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(20), default="for_review")
    version: Mapped[int] = mapped_column(default=0)
    journal_id: Mapped[UUID | None]
    reason: Mapped[str | None] = mapped_column(String(1000))
    peer_id: Mapped[UUID | None]


class BankRule(TenantRecord, Base):
    __tablename__ = "bank_rules"
    __table_args__ = {"schema": "app"}
    name: Mapped[str] = mapped_column(String(160))
    contains: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[UUID]
    priority: Mapped[int]
    version: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=False)
    preview_hash: Mapped[str | None] = mapped_column(String(64))
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class BankMatch(TenantRecord, Base):
    __tablename__ = "bank_matches"
    __table_args__ = {"schema": "app"}
    row_id: Mapped[UUID]
    account_id: Mapped[UUID]
    journal_id: Mapped[UUID]


class BankCorrection(TenantRecord, Base):
    __tablename__ = "bank_corrections"
    __table_args__ = {"schema": "app"}
    row_id: Mapped[UUID]
    accounting_date: Mapped[date]
    journal_id: Mapped[UUID]
    reason: Mapped[str] = mapped_column(String(1000))
    splits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_by: Mapped[UUID]


class Statement(TenantRecord, Base):
    __tablename__ = "statements"
    __table_args__ = {"schema": "app"}
    account_id: Mapped[UUID]
    as_of: Mapped[date]
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    outstanding: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    ledger_balance: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    evidence_id: Mapped[UUID]
    reviewer_id: Mapped[UUID]
