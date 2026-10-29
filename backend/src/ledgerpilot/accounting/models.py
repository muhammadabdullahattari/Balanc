"""Exact, tenant-linked accounting persistence."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base, TransactionId


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("business_id", "code"),
        CheckConstraint("kind IN ('asset','liability','equity','income','expense')"),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    control: Mapped[str | None] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=0)


class Period(Base):
    __tablename__ = "periods"
    __table_args__ = (
        CheckConstraint("start_date <= end_date"),
        UniqueConstraint("business_id", "start_date"),
        UniqueConstraint("business_id", "id"),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    locked: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    locked_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))


class Journal(Base):
    __tablename__ = "journals"
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("business_id", "idempotency_key"),
        UniqueConstraint("business_id", "reversal_of"),
        UniqueConstraint("business_id", "operation_key"),
        ForeignKeyConstraint(
            ["business_id", "reversal_of"], ["app.journals.business_id", "app.journals.id"]
        ),
        ForeignKeyConstraint(
            ["business_id", "period_id"], ["app.periods.business_id", "app.periods.id"]
        ),
        ForeignKeyConstraint(
            ["business_id", "policy_id"],
            ["app.policy_history.business_id", "app.policy_history.id"],
        ),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    accounting_date: Mapped[date] = mapped_column(Date, index=True)
    memo: Mapped[str] = mapped_column(String(2000))
    currency: Mapped[str] = mapped_column(String(3))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    reversal_of: Mapped[UUID | None]
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    operation_key: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"))
    status: Mapped[str] = mapped_column(String(20), default="posted", server_default="posted")
    version: Mapped[int] = mapped_column(default=0, server_default="0")
    period_id: Mapped[UUID | None]
    policy_id: Mapped[UUID | None]
    source_kind: Mapped[str] = mapped_column(
        String(40), default="manual_journal", server_default="manual_journal"
    )
    source_id: Mapped[UUID | None]
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    book: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    posting_xid: Mapped[str] = mapped_column(
        TransactionId(), server_default=text("pg_current_xact_id()")
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "journal_id"], ["app.journals.business_id", "app.journals.id"]
        ),
        ForeignKeyConstraint(
            ["business_id", "account_id"], ["app.accounts.business_id", "app.accounts.id"]
        ),
        CheckConstraint("debit >= 0 AND credit >= 0 AND ((debit > 0) <> (credit > 0))"),
        UniqueConstraint("journal_id", "position"),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(index=True)
    journal_id: Mapped[UUID] = mapped_column(index=True)
    account_id: Mapped[UUID] = mapped_column(index=True)
    position: Mapped[int]
    debit: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    credit: Mapped[Decimal] = mapped_column(Numeric(24, 6))
