"""Versioned retention decisions; eligibility never authorizes physical deletion."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import DateTime, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.audit.service import record
from ledgerpilot.database import Base
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import TenantRecord

Category = Literal["journals", "trade_documents", "evidence", "bank_records", "audit_records"]
ADMINS = {"owner", "accountant"}


class RetentionPolicy(TenantRecord, Base):
    __tablename__ = "retention_policies"
    __table_args__ = {"schema": "app"}
    category: Mapped[str]
    jurisdiction: Mapped[str]
    years: Mapped[int]
    version: Mapped[int]
    reference: Mapped[str]
    approved_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LegalHold(TenantRecord, Base):
    __tablename__ = "legal_holds"
    __table_args__ = {"schema": "app"}
    category: Mapped[str]
    reason: Mapped[str]
    placed_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[UUID | None]
    release_reason: Mapped[str | None]


class PolicyCreate(Schema):
    category: Category
    jurisdiction: str = Field(min_length=2, max_length=80)
    years: int = Field(ge=1, le=100)
    reference: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    expected_version: int = Field(ge=0)


class PolicyRead(Schema):
    id: UUID
    category: Category
    jurisdiction: str
    years: int
    version: int
    reference: str
    approved_by: UUID
    created_at: datetime


class HoldCreate(Schema):
    category: Literal[
        "all", "journals", "trade_documents", "evidence", "bank_records", "audit_records"
    ]
    reason: str = Field(min_length=1, max_length=1000)


class ReleaseHold(Schema):
    reason: str = Field(min_length=1, max_length=1000)


class HoldRead(Schema):
    id: UUID
    category: str
    reason: str
    placed_by: UUID
    created_at: datetime
    released_at: datetime | None
    released_by: UUID | None
    release_reason: str | None


class RetentionState(Schema):
    default_years: int = 10
    clock: str = "record_date"
    policies: list[PolicyRead]
    holds: list[HoldRead]
    disposal_enabled: bool = False


class EligibilityInput(Schema):
    category: Category
    jurisdiction: str = Field(min_length=2, max_length=80)
    record_date: date
    as_of: date


class Eligibility(Schema):
    retain_until: date
    policy_id: UUID | None
    policy_version: int
    active_hold_ids: list[UUID]
    retention_elapsed: bool
    eligible_for_disposal_review: bool
    deletion_authorized: bool = False


router = APIRouter(prefix="/businesses/{business_id}/retention", tags=["retention"])


def gate(request: Request, business_id: UUID) -> None:
    settings = request.app.state.settings
    if (
        settings.app_env not in {"local", "test"}
        or business_id not in settings.p2_retention_business_ids
    ):
        raise DomainError("ERR_NOT_FOUND", "Retention controls are unavailable.", 404)


@router.get("", response_model=RetentionState)
def state(business_id: UUID, request: Request, identity: Identity, db: DB) -> RetentionState:
    authorize(db, identity, business_id, ADMINS)
    gate(request, business_id)
    policies = list(
        db.scalars(
            select(RetentionPolicy)
            .where(RetentionPolicy.business_id == business_id)
            .order_by(
                RetentionPolicy.category,
                RetentionPolicy.jurisdiction,
                RetentionPolicy.version.desc(),
            )
            .ext(distinct_on(RetentionPolicy.category, RetentionPolicy.jurisdiction))
        )
    )
    holds = list(
        db.scalars(
            select(LegalHold)
            .where(LegalHold.business_id == business_id)
            .order_by(LegalHold.created_at.desc(), LegalHold.id)
            .limit(200)
        )
    )
    return RetentionState(
        policies=[PolicyRead.model_validate(p) for p in policies],
        holds=[HoldRead.model_validate(h) for h in holds],
    )


@router.post("/policies", response_model=PolicyRead, status_code=201)
def configure(
    business_id: UUID, data: PolicyCreate, request: Request, identity: Identity, db: DB
) -> PolicyRead:
    authorize(db, identity, business_id, ADMINS)
    gate(request, business_id)
    get_business(db, business_id, lock=True)
    jurisdiction = data.jurisdiction.strip().upper()
    version = (
        db.scalar(
            select(func.max(RetentionPolicy.version)).where(
                RetentionPolicy.business_id == business_id,
                RetentionPolicy.category == data.category,
                RetentionPolicy.jurisdiction == jurisdiction,
            )
        )
        or 0
    )
    if version != data.expected_version:
        raise DomainError("ERR_VERSION", "The policy changed. Refresh before saving.", 409)
    count = (
        db.scalar(
            select(func.count())
            .select_from(RetentionPolicy)
            .where(RetentionPolicy.business_id == business_id)
        )
        or 0
    )
    if count >= 1000:
        raise DomainError("ERR_QUOTA", "Retention policy history reached its local limit.", 413)
    policy = RetentionPolicy(
        business_id=business_id,
        category=data.category,
        jurisdiction=jurisdiction,
        years=data.years,
        version=version + 1,
        reference=data.reference.strip(),
        approved_by=identity.user.id,
    )
    db.add(policy)
    db.flush()
    record(db, identity.user.id, "retention.policy_recorded", business_id, policy.id)
    return PolicyRead.model_validate(policy)


@router.post("/holds", response_model=HoldRead, status_code=201)
def place_hold(
    business_id: UUID, data: HoldCreate, request: Request, identity: Identity, db: DB
) -> HoldRead:
    authorize(db, identity, business_id, ADMINS)
    gate(request, business_id)
    get_business(db, business_id, lock=True)
    count = (
        db.scalar(
            select(func.count()).select_from(LegalHold).where(LegalHold.business_id == business_id)
        )
        or 0
    )
    if count >= 200:
        raise DomainError("ERR_QUOTA", "Legal hold history reached its local limit.", 413)
    hold = LegalHold(business_id=business_id, placed_by=identity.user.id, **data.model_dump())
    db.add(hold)
    db.flush()
    record(db, identity.user.id, "retention.hold_placed", business_id, hold.id)
    return HoldRead.model_validate(hold)


@router.post("/holds/{hold_id}/release", response_model=HoldRead)
def release_hold(
    business_id: UUID,
    hold_id: UUID,
    data: ReleaseHold,
    request: Request,
    identity: Identity,
    db: DB,
) -> HoldRead:
    authorize(db, identity, business_id, ADMINS)
    gate(request, business_id)
    get_business(db, business_id, lock=True)
    hold = db.scalar(
        select(LegalHold).where(LegalHold.business_id == business_id, LegalHold.id == hold_id)
    )
    if hold is None:
        raise DomainError("ERR_NOT_FOUND", "Legal hold not found.", 404)
    if hold.released_at is None:
        hold.released_at = db.execute(select(func.statement_timestamp())).scalar_one()
        hold.released_by = identity.user.id
        hold.release_reason = data.reason
        db.flush()
        record(db, identity.user.id, "retention.hold_released", business_id, hold.id)
    return HoldRead.model_validate(hold)


@router.post("/preview", response_model=Eligibility)
def preview(
    business_id: UUID, data: EligibilityInput, request: Request, identity: Identity, db: DB
) -> Eligibility:
    authorize(db, identity, business_id, ADMINS)
    gate(request, business_id)
    get_business(db, business_id, lock=True)
    if data.as_of < data.record_date or data.record_date.year > 9899:
        raise DomainError(
            "ERR_DATE_RANGE", "Use an assessment date on or after the record date.", 422
        )
    policy = db.scalar(
        select(RetentionPolicy)
        .where(
            RetentionPolicy.business_id == business_id,
            RetentionPolicy.category == data.category,
            RetentionPolicy.jurisdiction == data.jurisdiction.strip().upper(),
        )
        .order_by(RetentionPolicy.version.desc())
        .limit(1)
    )
    years = policy.years if policy else 10
    try:
        until = data.record_date.replace(year=data.record_date.year + years)
    except ValueError:  # February 29: retain through March 1 in non-leap years.
        until = date(data.record_date.year + years, 3, 1)
    holds = list(
        db.scalars(
            select(LegalHold.id).where(
                LegalHold.business_id == business_id,
                LegalHold.released_at.is_(None),
                LegalHold.category.in_(("all", data.category)),
            )
        )
    )
    return Eligibility(
        retain_until=until,
        policy_id=policy.id if policy else None,
        policy_version=policy.version if policy else 0,
        active_hold_ids=holds,
        retention_elapsed=data.as_of >= until,
        eligible_for_disposal_review=data.as_of >= until and not holds,
    )
