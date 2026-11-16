"""Immutable period-close packages and independent review."""

from datetime import date, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select

from ledgerpilot.accounting.models import Period
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.identity.security import now
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.workspace import micro_close, service
from ledgerpilot.workspace.models import CloseTask, ReportRun

router = APIRouter(prefix="/businesses/{business_id}/close-packages")


class ClosePackage(BaseModel):
    id: UUID
    period_id: UUID
    start: date
    end: date
    locked: bool
    period_version: int
    prepared_by: UUID
    prepared_at: datetime
    readiness: dict[str, Any]
    nightly_report_id: UUID | None
    tasks: list[dict[str, Any]]


class SignOff(BaseModel):
    package_id: UUID
    reviewed_by: UUID
    reviewed_at: datetime


class ClosePackageEntry(BaseModel):
    package: ClosePackage
    review: SignOff | None
    can_sign: bool


class ClosePackageList(BaseModel):
    items: list[ClosePackageEntry]
    truncated: bool


def eligible(package: dict[str, Any]) -> bool:
    return bool(
        package["locked"]
        and package["readiness"]["ready"]
        and all(not task["required"] or task["state"] == "approved" for task in package["tasks"])
    )


@router.get("", response_model=ClosePackageList)
def packages(
    business_id: UUID, period_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    micro_close.require_rollout(request.app.state.settings, business_id)
    period = db.get(Period, period_id)
    if period is None or period.business_id != business_id:
        raise DomainError("ERR_NOT_FOUND", "Period not found.", 404)
    rows = list(
        db.scalars(
            select(ReportRun)
            .where(
                ReportRun.business_id == business_id,
                ReportRun.kind == "period_close_package",
                ReportRun.result["period_id"].astext == str(period_id),
            )
            .order_by(ReportRun.created_at.desc(), ReportRun.id)
            .limit(51)
        )
    )
    reviews = {
        row.id: row.result
        for row in db.scalars(
            select(ReportRun).where(
                ReportRun.business_id == business_id,
                ReportRun.kind == "period_close_review",
                ReportRun.id.in_([review_id(row.id) for row in rows[:50]]),
            )
        )
    }
    return {
        "items": [
            {
                "package": row.result,
                "review": reviews.get(review_id(row.id)),
                "can_sign": row.created_by != identity.user.id
                and period.locked
                and period.version == row.result["period_version"]
                and eligible(row.result)
                and review_id(row.id) not in reviews,
            }
            for row in rows[:50]
        ],
        "truncated": len(rows) > 50,
    }


def package_row(db: DB, business_id: UUID, package_id: UUID, kind: str) -> ReportRun | None:
    return db.scalar(
        select(ReportRun).where(
            ReportRun.business_id == business_id, ReportRun.id == package_id, ReportRun.kind == kind
        )
    )


@router.post("", status_code=201, response_model=ClosePackage)
def prepare(
    business_id: UUID, period_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    micro_close.require_rollout(request.app.state.settings, business_id)
    get_business(db, business_id, lock=True)
    period = db.scalar(
        select(Period).where(Period.business_id == business_id, Period.id == period_id)
    )
    if period is None:
        raise DomainError("ERR_NOT_FOUND", "Period not found.", 404)
    tasks = list(
        db.scalars(
            select(CloseTask)
            .where(CloseTask.business_id == business_id, CloseTask.period_id == period_id)
            .order_by(CloseTask.id)
            .limit(1001)
        )
    )
    if len(tasks) > 1000:
        raise DomainError("ERR_QUOTA", "Period exceeds 1,000 close tasks.", 413)
    readiness = service.readiness_report(db, business_id, period.end_date)
    service.store_report(db, identity.user.id, business_id, readiness)
    nightly = micro_close.lookup(db, business_id, period.end_date)
    identifier = uuid4()
    result = jsonable_encoder(
        {
            "id": identifier,
            "period_id": period.id,
            "start": period.start_date,
            "end": period.end_date,
            "locked": period.locked,
            "period_version": period.version,
            "prepared_by": identity.user.id,
            "prepared_at": now(),
            "readiness": readiness,
            "nightly_report_id": nightly.id if nightly else None,
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "state": t.state,
                    "required": t.required,
                    "prepared_by": t.prepared_by,
                    "reviewed_by": t.reviewed_by,
                    "evidence_id": t.evidence_id,
                    "comment": t.comment,
                    "review_comment": t.review_comment,
                }
                for t in tasks
            ],
        }
    )
    db.add(
        ReportRun(
            id=identifier,
            business_id=business_id,
            kind="period_close_package",
            result=result,
            created_by=identity.user.id,
        )
    )
    record(db, identity.user.id, "close.package_prepared", business_id, identifier)
    return dict(result)


def review_id(package_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ledgerpilot:period-close-review:{package_id}")


class ClosePackageDetail(BaseModel):
    package: ClosePackage
    review: SignOff | None


@router.get("/{package_id}", response_model=ClosePackageDetail)
def read(
    business_id: UUID, package_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    micro_close.require_rollout(request.app.state.settings, business_id)
    item = package_row(db, business_id, package_id, "period_close_package")
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Close package not found.", 404)
    review = package_row(db, business_id, review_id(package_id), "period_close_review")
    return {"package": item.result, "review": review.result if review else None}


@router.post("/{package_id}/review", response_model=SignOff)
def review(
    business_id: UUID, package_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    package = read(business_id, package_id, request, identity, db)["package"]
    if package["prepared_by"] == str(identity.user.id):
        raise DomainError("ERR_REVIEWER", "A different member must review this package.", 403)
    if not eligible(package):
        raise DomainError(
            "ERR_CLOSE_NOT_READY", "The captured period is not ready for sign-off.", 409
        )
    period = db.get(Period, UUID(package["period_id"]))
    if period is None or not period.locked or period.version != package["period_version"]:
        raise DomainError("ERR_CONFLICT", "Prepare a new package for the current period.", 409)
    identifier = review_id(package_id)
    existing = package_row(db, business_id, identifier, "period_close_review")
    if existing:
        return existing.result
    result = {
        "package_id": str(package_id),
        "reviewed_by": str(identity.user.id),
        "reviewed_at": now().isoformat(),
    }
    db.add(
        ReportRun(
            id=identifier,
            business_id=business_id,
            kind="period_close_review",
            result=result,
            created_by=identity.user.id,
        )
    )
    record(db, identity.user.id, "close.package_reviewed", business_id, package_id)
    return dict(result)
