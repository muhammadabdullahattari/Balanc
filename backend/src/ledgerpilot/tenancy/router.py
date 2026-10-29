"""Business selection is always authorized against durable membership."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import Date, func, select, text

from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.identity.schemas import TokenRequest
from ledgerpilot.shared import contracts
from ledgerpilot.shared.responses import BusinessRead, BusinessSummary, Identifier, Page
from ledgerpilot.tenancy import service
from ledgerpilot.tenancy.models import Business, PolicyHistory
from ledgerpilot.tenancy.schemas import BusinessCreate, InviteCreate, PolicyChange

router = APIRouter(tags=["businesses"])


@router.post("/businesses", status_code=201, response_model=Identifier)
def create(data: BusinessCreate, identity: Identity, db: DB) -> dict[str, str]:
    return {"id": str(service.create(db, identity, data))}


@router.get("/businesses", response_model=Page[BusinessSummary])
def businesses(
    identity: Identity, db: DB, limit: int = Query(50, ge=1, le=200), cursor: UUID | None = None
) -> dict[str, object]:
    active_basis = (
        select(PolicyHistory.basis)
        .where(
            PolicyHistory.business_id == Business.id,
            PolicyHistory.effective_date <= func.timezone(Business.timezone, func.now()).cast(Date),
        )
        .order_by(PolicyHistory.effective_date.desc(), PolicyHistory.version.desc())
        .limit(1)
        .scalar_subquery()
    )
    query = (
        select(Business, func.coalesce(active_basis, Business.basis))
        .order_by(Business.id)
        .limit(limit + 1)
    )
    if cursor:
        query = query.where(Business.id > cursor)
    rows = [(cast(Business, row[0]), str(row[1])) for row in db.execute(query)]
    return {
        "items": [
            {
                "id": str(b.id),
                "name": b.name,
                "currency": b.currency,
                "basis": basis,
                "version": b.version,
            }
            for b, basis in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1][0].id) if len(rows) > limit else None,
    }


@router.get("/businesses/{business_id}", response_model=BusinessRead)
def business(business_id: UUID, identity: Identity, db: DB) -> dict[str, object]:
    service.authorize(db, identity, business_id)
    b = service.get_business(db, business_id)
    policy = service.effective_policy(db, b)
    return {
        "id": str(b.id),
        "name": b.name,
        "currency": b.currency,
        "currency_precision": b.currency_precision,
        "timezone": b.timezone,
        "basis": policy.basis if policy else b.basis,
        "version": b.version,
        "policy_version": policy.version if policy else 1,
        "fiscal_month": b.fiscal_month,
        "jurisdiction": policy.jurisdiction if policy else b.jurisdiction,
    }


@router.post(
    "/businesses/{business_id}/invitations",
    status_code=202,
    response_model=contracts.responses["tenancy.invite"],
    response_model_exclude_unset=True,
)
def invite(
    business_id: UUID, data: InviteCreate, request: Request, identity: Identity, db: DB
) -> dict[str, str]:
    service.invite(db, request.app.state.settings, identity, business_id, data)
    return {"status": "invitation_sent"}


@router.post(
    "/invitations/accept",
    response_model=contracts.responses["tenancy.accept"],
    response_model_exclude_unset=True,
)
def accept(data: TokenRequest, identity: Identity, db: DB) -> dict[str, str]:
    return {"business_id": str(service.accept(db, identity, data.token.get_secret_value()))}


@router.get(
    "/businesses/{business_id}/policies",
    response_model=contracts.responses["tenancy.policies"],
    response_model_exclude_unset=True,
)
def policies(
    business_id: UUID,
    identity: Identity,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    after_version: int = Query(0, ge=0),
) -> dict[str, object]:
    service.authorize(db, identity, business_id)
    rows = list(
        db.scalars(
            select(PolicyHistory)
            .where(PolicyHistory.business_id == business_id, PolicyHistory.version > after_version)
            .order_by(PolicyHistory.version)
            .limit(limit + 1)
        )
    )
    return {
        "items": [
            {
                "version": p.version,
                "effective_date": str(p.effective_date),
                "basis": p.basis,
                "jurisdiction": p.jurisdiction,
            }
            for p in rows[:limit]
        ],
        "next_version": rows[limit - 1].version if len(rows) > limit else None,
    }


@router.post(
    "/businesses/{business_id}/policies",
    status_code=201,
    response_model=contracts.responses["tenancy.change_policy"],
    response_model_exclude_unset=True,
)
def change_policy(
    business_id: UUID, data: PolicyChange, identity: Identity, db: DB
) -> dict[str, int]:
    service.authorize(db, identity, business_id, {"accountant"})
    version: int = db.execute(
        text("SELECT app.change_policy(:b,:basis,:j,:d,:v)"),
        {
            "b": business_id,
            "basis": data.basis,
            "j": data.jurisdiction,
            "d": data.effective_date,
            "v": data.expected_version,
        },
    ).scalar_one()
    return {"policy_version": version}
