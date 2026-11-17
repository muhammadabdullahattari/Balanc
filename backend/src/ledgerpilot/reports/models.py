"""Balance summaries model for materialized/incrementally maintained account totals."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base


class BalanceSummary(Base):
    __tablename__ = "balance_summaries"
    __table_args__ = {"schema": "app"}

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.businesses.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.accounts.id", ondelete="CASCADE"), primary_key=True
    )
    ledger_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    debit_total: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0.0000"), nullable=False
    )
    credit_total: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0.0000"), nullable=False
    )
    net_balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0.0000"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
