"""Businesses are explicit authorization and accounting boundaries."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint("currency_precision BETWEEN 0 AND 6"),
        CheckConstraint("basis IN ('cash','accrual')"),
        CheckConstraint("fiscal_month BETWEEN 1 AND 12"),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3))
    currency_precision: Mapped[int]
    timezone: Mapped[str] = mapped_column(String(100))
    basis: Mapped[str] = mapped_column(String(10))
    fiscal_month: Mapped[int] = mapped_column(default=1)
    jurisdiction: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=0)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner','accountant','bookkeeper','reviewer','auditor')"),
        {"schema": "app"},
    )
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20))
    can_post: Mapped[bool] = mapped_column(default=False)
    can_invite: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=0, server_default="0")


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = {"schema": "app"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyHistory(Base):
    __tablename__ = "policy_history"
    __table_args__ = (
        UniqueConstraint("business_id", "version"),
        UniqueConstraint("business_id", "id"),
        {"schema": "app"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("app.businesses.id"), index=True)
    version: Mapped[int]
    effective_date: Mapped[date] = mapped_column(Date)
    basis: Mapped[str] = mapped_column(String(10))
    jurisdiction: Mapped[str] = mapped_column(String(100))
