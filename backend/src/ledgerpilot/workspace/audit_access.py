"""Explicit, expiring report shares; never create workspace membership for a recipient."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import DateTime, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.audit.service import record
from ledgerpilot.database import Base, set_context
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import TenantRecord
from ledgerpilot.workspace.models import ReportRun


class AuditGrant(TenantRecord, Base):
    __tablename__ = "audit_report_grants"
    __table_args__ = {"schema": "app"}
    recipient_id: Mapped[UUID]
    granted_by: Mapped[UUID]
    report_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PGUUID(as_uuid=True)))
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditGrantCreate(Schema):
    recipient_id: UUID
    report_ids: list[UUID] = Field(min_length=1, max_length=20)
    hours: int = Field(ge=1, le=720)
    reason: str = Field(min_length=1, max_length=1000)


class AuditGrantRead(Schema):
    id: UUID
    business_id: UUID
    recipient_id: UUID
    report_ids: list[UUID]
    expires_at: datetime
    revoked_at: datetime | None


class AuditGrantPage(Schema):
    items: list[AuditGrantRead]
    next_cursor: UUID | None


router = APIRouter(tags=["audit-access"])


def gate(request: Request, bid: UUID) -> None:
    settings = request.app.state.settings
    if settings.app_env not in {"local", "test"} or bid not in settings.p2_audit_business_ids:
        raise DomainError("ERR_NOT_FOUND", "Scoped audit sharing is unavailable.", 404)


@router.post(
    "/businesses/{business_id}/audit-grants", response_model=AuditGrantRead, status_code=201
)
def grant(
    business_id: UUID, data: AuditGrantCreate, request: Request, identity: Identity, db: DB
) -> AuditGrantRead:
    authorize(db, identity, business_id, {"owner", "accountant"})
    gate(request, business_id)
    get_business(db, business_id, lock=True)
    ids = list(
        db.scalars(
            select(ReportRun.id).where(
                ReportRun.business_id == business_id, ReportRun.id.in_(data.report_ids)
            )
        )
    )
    if len(ids) != len(data.report_ids):
        raise DomainError("ERR_REPORT", "Select distinct saved reports from this workspace.", 422)
    count = (
        db.scalar(
            select(func.count())
            .select_from(AuditGrant)
            .where(AuditGrant.business_id == business_id)
        )
        or 0
    )
    if count >= 500:
        raise DomainError("ERR_QUOTA", "Audit share history reached its local limit.", 413)
    at: datetime = db.execute(select(func.statement_timestamp())).scalar_one()
    item = AuditGrant(
        business_id=business_id,
        recipient_id=data.recipient_id,
        granted_by=identity.user.id,
        report_ids=ids,
        reason=data.reason,
        created_at=at,
        expires_at=at + timedelta(hours=data.hours),
    )
    db.add(item)
    db.flush()
    record(db, identity.user.id, "audit_access.granted", business_id, item.id)
    return AuditGrantRead.model_validate(item)


@router.get("/businesses/{business_id}/audit-grants", response_model=AuditGrantPage)
def grants(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> AuditGrantPage:
    authorize(db, identity, business_id, {"owner", "accountant"})
    gate(request, business_id)
    query = select(AuditGrant).where(AuditGrant.business_id == business_id)
    if cursor:
        query = query.where(AuditGrant.id > cursor)
    rows = list(db.scalars(query.order_by(AuditGrant.id).limit(limit + 1)))
    return AuditGrantPage(
        items=[AuditGrantRead.model_validate(r) for r in rows[:limit]],
        next_cursor=rows[limit - 1].id if len(rows) > limit else None,
    )


@router.post(
    "/businesses/{business_id}/audit-grants/{grant_id}/revoke", response_model=AuditGrantRead
)
def revoke(
    business_id: UUID, grant_id: UUID, request: Request, identity: Identity, db: DB
) -> AuditGrantRead:
    authorize(db, identity, business_id, {"owner", "accountant"})
    gate(request, business_id)
    get_business(db, business_id, lock=True)
    item = db.scalar(
        select(AuditGrant).where(AuditGrant.business_id == business_id, AuditGrant.id == grant_id)
    )
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Audit share not found.", 404)
    if item.revoked_at is None:
        item.revoked_at = db.execute(select(func.statement_timestamp())).scalar_one()
        db.flush()
        record(db, identity.user.id, "audit_access.revoked", business_id, item.id)
    return AuditGrantRead.model_validate(item)


@router.get(
    "/audit-access/{grant_id}/reports/{report_id}",
    response_model=contracts.responses["workspace.report"],
    response_model_exclude_unset=True,
)
def read_shared(
    grant_id: UUID, report_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    if not identity.family.mfa:
        raise DomainError(
            "ERR_MFA_REQUIRED", "Use an MFA-authenticated session to read workpapers.", 403
        )
    item = db.scalar(
        select(AuditGrant).where(
            AuditGrant.id == grant_id,
            AuditGrant.recipient_id == identity.user.id,
            AuditGrant.revoked_at.is_(None),
            AuditGrant.expires_at > func.statement_timestamp(),
        )
    )
    if item is None or report_id not in item.report_ids:
        raise DomainError("ERR_NOT_FOUND", "This report share is unavailable or expired.", 404)
    gate(request, item.business_id)
    set_context(db, identity.user.id, item.business_id)
    report = db.scalar(
        select(ReportRun).where(
            ReportRun.business_id == item.business_id, ReportRun.id == report_id
        )
    )
    if report is None:
        raise DomainError("ERR_NOT_FOUND", "This report share is unavailable or expired.", 404)
    record(
        db,
        identity.user.id,
        "audit_access.report_read",
        item.business_id,
        item.id,
        {"reason": f"report:{report_id}"},
    )
    return report.result
