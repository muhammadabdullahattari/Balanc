"""Tenant email diagnostics, operator-recorded feedback and scoped delegation."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import DateTime, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from ledgerpilot.audit.service import record
from ledgerpilot.database import Base
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.notifications.email_outbox import BusinessEmailJob, EmailFeedback
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade.models import TenantRecord

ADMINS = {"owner", "accountant"}
ATTENTION_STATES = ("failed", "uncertain", "suppressed")


class SupportGrant(TenantRecord, Base):
    __tablename__ = "business_email_support_grants"
    __table_args__ = {"schema": "app"}
    reviewer_id: Mapped[UUID]
    granted_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrantCreate(Schema):
    reviewer_id: UUID
    hours: int = Field(ge=1, le=24)


class GrantRead(Schema):
    id: UUID
    reviewer_id: UUID
    expires_at: datetime
    revoked_at: datetime | None


class FeedbackCreate(Schema):
    kind: Literal["bounced", "complained"]


class FeedbackRead(Schema):
    id: UUID
    kind: Literal["bounced", "complained"]


class DiagnosticJob(Schema):
    id: UUID
    kind: str
    state: str
    attempts: int
    outcome_code: str | None
    created_at: datetime
    feedback: list[str]


class Diagnostics(Schema):
    window_start: datetime
    counts: dict[str, int]
    attention: list[DiagnosticJob]
    grants: list[GrantRead]


def active_grant(db: Session, bid: UUID, actor: UUID) -> SupportGrant | None:
    return db.scalar(
        select(SupportGrant).where(
            SupportGrant.business_id == bid,
            SupportGrant.reviewer_id == actor,
            SupportGrant.revoked_at.is_(None),
            SupportGrant.expires_at > func.statement_timestamp(),
        )
    )


def diagnostics(db: Session, identity: Principal, bid: UUID, administrator: bool) -> Diagnostics:
    at: datetime = db.execute(select(func.statement_timestamp())).scalar_one()
    grants = []
    if administrator:
        grants = list(
            db.scalars(
                select(SupportGrant)
                .where(SupportGrant.business_id == bid)
                .order_by(SupportGrant.created_at.desc(), SupportGrant.id)
                .limit(50)
            )
        )
    else:
        grant = active_grant(db, bid, identity.user.id)
        if grant is None:
            raise DomainError("ERR_FORBIDDEN", "An active email support grant is required.", 403)
        record(db, identity.user.id, "business_email.support_viewed", bid, grant.id)

    since = at - timedelta(days=7)
    scope = (BusinessEmailJob.business_id == bid, BusinessEmailJob.created_at >= since)
    counts = {
        state: count
        for state, count in db.execute(
            select(BusinessEmailJob.state, func.count())
            .where(*scope)
            .group_by(BusinessEmailJob.state)
        )
    }
    query = select(BusinessEmailJob).where(*scope)
    if not administrator:
        query = query.where(BusinessEmailJob.state.in_(ATTENTION_STATES))
    else:
        query = query.where(BusinessEmailJob.state.in_((*ATTENTION_STATES, "sent")))
    jobs = list(
        db.scalars(
            query.order_by(
                BusinessEmailJob.state == "sent",
                BusinessEmailJob.created_at.desc(),
                BusinessEmailJob.id,
            ).limit(50)
        )
    )
    feedback: dict[UUID, list[str]] = {}
    if administrator and jobs:
        for job_id, kind in db.execute(
            select(EmailFeedback.job_id, EmailFeedback.kind)
            .where(EmailFeedback.business_id == bid, EmailFeedback.job_id.in_([j.id for j in jobs]))
            .order_by(EmailFeedback.kind)
        ):
            feedback.setdefault(job_id, []).append(kind)
    return Diagnostics(
        window_start=since,
        counts=counts,
        attention=[
            DiagnosticJob(
                id=job.id,
                kind=job.kind,
                state=job.state,
                attempts=job.attempts,
                outcome_code=job.outcome_code,
                created_at=job.created_at,
                feedback=feedback.get(job.id, []),
            )
            for job in jobs
        ],
        grants=[GrantRead.model_validate(grant) for grant in grants],
    )


def record_feedback(
    db: Session, actor: UUID, bid: UUID, job_id: UUID, data: FeedbackCreate
) -> FeedbackRead:
    job = db.scalar(
        select(BusinessEmailJob).where(
            BusinessEmailJob.business_id == bid, BusinessEmailJob.id == job_id
        )
    )
    if job is None:
        raise DomainError("ERR_NOT_FOUND", "Email job not found.", 404)
    if job.state not in {"sent", "uncertain", "failed"}:
        raise DomainError("ERR_EMAIL_FEEDBACK", "Record feedback after a delivery attempt.", 409)
    identifier = db.scalar(
        insert(EmailFeedback)
        .values(
            business_id=bid,
            job_id=job_id,
            recipient_id=job.recipient_id,
            kind=data.kind,
            recorded_by=actor,
        )
        .on_conflict_do_nothing(index_elements=["job_id", "kind"])
        .returning(EmailFeedback.id)
    )
    if identifier is not None:
        record(
            db, actor, "business_email.feedback_recorded", bid, identifier, {"status": data.kind}
        )
    else:
        identifier = db.execute(
            select(EmailFeedback.id).where(
                EmailFeedback.business_id == bid,
                EmailFeedback.job_id == job_id,
                EmailFeedback.kind == data.kind,
            )
        ).scalar_one()
    return FeedbackRead(id=identifier, kind=data.kind)


def grant_support(db: Session, actor: UUID, bid: UUID, data: GrantCreate) -> GrantRead:
    get_business(db, bid, lock=True)
    reviewer = db.scalar(
        text(
            "SELECT user_id FROM app.list_members(:bid) "
            "WHERE user_id=:uid AND role='reviewer' AND active"
        ),
        {"bid": bid, "uid": data.reviewer_id},
    )
    if reviewer is None:
        raise DomainError("ERR_NOT_FOUND", "Choose an active workspace reviewer.", 404)
    at: datetime = db.execute(select(func.statement_timestamp())).scalar_one()
    previous = db.scalar(
        select(SupportGrant).where(
            SupportGrant.business_id == bid,
            SupportGrant.reviewer_id == reviewer,
            SupportGrant.revoked_at.is_(None),
        )
    )
    if previous is not None:
        if previous.expires_at > at:
            raise DomainError("ERR_SUPPORT_GRANT", "This reviewer already has active access.", 409)
        previous.revoked_at = at
        db.flush()
    grant = SupportGrant(
        business_id=bid,
        reviewer_id=reviewer,
        granted_by=actor,
        created_at=at,
        expires_at=at + timedelta(hours=data.hours),
    )
    db.add(grant)
    db.flush()
    record(
        db,
        actor,
        "business_email.support_granted",
        bid,
        grant.id,
        {"expires_at": grant.expires_at.isoformat()},
    )
    return GrantRead.model_validate(grant)


def revoke_support(db: Session, actor: UUID, bid: UUID, grant_id: UUID) -> GrantRead:
    get_business(db, bid, lock=True)
    grant = db.scalar(
        select(SupportGrant).where(SupportGrant.business_id == bid, SupportGrant.id == grant_id)
    )
    if grant is None:
        raise DomainError("ERR_NOT_FOUND", "Support grant not found.", 404)
    if grant.revoked_at is None:
        grant.revoked_at = db.execute(select(func.statement_timestamp())).scalar_one()
        record(db, actor, "business_email.support_revoked", bid, grant.id)
    return GrantRead.model_validate(grant)
