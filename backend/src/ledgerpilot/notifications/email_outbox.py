"""Durable, opt-in reminders for the current workspace member."""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from ledgerpilot.config import Settings
from ledgerpilot.database import Base
from ledgerpilot.identity.security import now
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Business
from ledgerpilot.trade.models import TenantRecord, TradeDocument
from ledgerpilot.trade.repository import due_snapshots
from ledgerpilot.workspace.models import EvidenceLink, Preference

KINDS = ("overdue_invoices", "missing_evidence")
ROLES = {"owner", "accountant", "bookkeeper"}
ATTEMPT_LIMIT = 3
SEND_WINDOW = timedelta(minutes=2)
MAX_AGE = timedelta(hours=24)


class BusinessEmailJob(TenantRecord, Base):
    __tablename__ = "business_email_jobs"
    __table_args__ = (
        UniqueConstraint("business_id", "recipient_id", "kind", "review_date"),
        {"schema": "app"},
    )
    recipient_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    review_date: Mapped[date]
    state: Mapped[str] = mapped_column(String(20), server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    outcome_code: Mapped[str | None] = mapped_column(String(50))


class EmailFeedback(TenantRecord, Base):
    __tablename__ = "business_email_feedback"
    __table_args__ = {"schema": "app"}
    job_id: Mapped[UUID]
    recipient_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(16))
    recorded_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


def suppressed(db: Session, bid: UUID, recipient: UUID) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    EmailFeedback.business_id == bid, EmailFeedback.recipient_id == recipient
                )
            )
        )
    )


def local_delivery_available(settings: Settings, business_id: UUID) -> bool:
    """Enable the implemented Mailpit profile on an explicitly approved business."""
    return (
        settings.app_env in {"local", "test"}
        and settings.allows_p2_email_delivery(business_id)
        and settings.smtp_host in {"127.0.0.1", "localhost"}
        and settings.smtp_port == 1025
        and not settings.smtp_starttls
        and settings.smtp_username is None
        and settings.smtp_password is None
    )


def preferences(db: Session, bid: UUID, recipient: UUID) -> Preference | None:
    return db.scalar(
        select(Preference).where(Preference.business_id == bid, Preference.user_id == recipient)
    )


def opted_in(preference: Preference | None) -> bool:
    return bool(preference and preference.email_reminders and preference.reminders)


def is_quiet(at: datetime, business: Business, preference: Preference) -> bool:
    hour = at.astimezone(ZoneInfo(business.timezone)).hour
    start, end = preference.quiet_start, preference.quiet_end
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def eligible_count(db: Session, bid: UUID, as_of: date, kind: str) -> int:
    query = select(TradeDocument.id).where(
        TradeDocument.business_id == bid,
        TradeDocument.kind == "invoice",
        TradeDocument.due_date < as_of,
        TradeDocument.disputed.is_(False),
    )
    if kind == "missing_evidence":
        query = query.where(
            ~exists().where(
                EvidenceLink.business_id == bid,
                EvidenceLink.resource_kind == "trade_document",
                EvidenceLink.resource_id == TradeDocument.id,
            )
        )
    ids = list(db.scalars(query.limit(10001)))
    if len(ids) > 10000:
        raise DomainError("ERR_QUOTA", "The pilot email review exceeds 10,000 invoices.", 413)
    snapshots = due_snapshots(db, bid, [as_of, date.max], ids)
    return sum(
        snapshots[as_of].get(did, 0) > 0 and snapshots[date.max].get(did, 0) > 0 for did in ids
    )


def enqueue(
    db: Session, settings: Settings, business: Business, recipient: UUID, as_of: date
) -> int:
    bid = business.id
    if (
        not local_delivery_available(settings, bid)
        or as_of != now().astimezone(ZoneInfo(business.timezone)).date()
        or not opted_in(preferences(db, bid, recipient))
        or suppressed(db, bid, recipient)
    ):
        return 0
    kinds = [kind for kind in KINDS if eligible_count(db, bid, as_of, kind)]
    if not kinds:
        return 0
    jobs = db.scalars(
        insert(BusinessEmailJob)
        .values(
            [
                {"business_id": bid, "recipient_id": recipient, "kind": kind, "review_date": as_of}
                for kind in kinds
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["business_id", "recipient_id", "kind", "review_date"]
        )
        .returning(BusinessEmailJob.id)
    )
    return len(jobs.all())


def history(db: Session, bid: UUID, recipient: UUID) -> list[dict[str, Any]]:
    jobs = db.scalars(
        select(BusinessEmailJob)
        .where(BusinessEmailJob.business_id == bid, BusinessEmailJob.recipient_id == recipient)
        .order_by(BusinessEmailJob.created_at.desc(), BusinessEmailJob.id)
        .limit(50)
    )
    return [
        {
            "job_id": str(job.id),
            "kind": job.kind,
            "state": job.state,
            "attempts": job.attempts,
            "code": job.outcome_code,
        }
        for job in jobs
    ]
