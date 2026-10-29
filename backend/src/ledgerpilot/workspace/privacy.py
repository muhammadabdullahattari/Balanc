"""Independent, auditable P1 privacy fulfillment and retention decisions."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.workspace.models import PrivacyRequest, PrivacyReview


class ReviewRequest(Schema):
    outcome: Literal["export_ready", "retained", "requires_followup"]
    reason: str = Field(min_length=1, max_length=1000)
    policy_reference: str = Field(min_length=1, max_length=200)
    retained_categories: list[str] = Field(default_factory=list, max_length=20)
    retain_until: date | None = None
    legal_hold: bool = False


def statuses(db: Session, bid: UUID, request_ids: list[UUID]) -> dict[UUID, str]:
    result: dict[UUID, str] = {}
    for row in db.scalars(
        select(PrivacyReview)
        .where(PrivacyReview.business_id == bid, PrivacyReview.request_id.in_(request_ids))
        .order_by(PrivacyReview.created_at.desc(), PrivacyReview.id.desc())
    ):
        result.setdefault(row.request_id, row.outcome)
    return result


def review(
    db: Session, who: Principal, bid: UUID, rid: UUID, data: ReviewRequest
) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant"})
    get_business(db, bid, lock=True)
    item = db.scalar(
        select(PrivacyRequest).where(PrivacyRequest.business_id == bid, PrivacyRequest.id == rid)
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Privacy request not found.", 404)
    if item.user_id == who.user.id:
        raise DomainError("ERR_REVIEWER", "An independent owner/accountant must review.", 403)
    if data.outcome == "export_ready" and item.kind != "export":
        raise DomainError(
            "ERR_PRIVACY_STATE", "Only export requests may be marked export-ready.", 422
        )
    if (data.outcome == "retained" or data.legal_hold) and not data.retained_categories:
        raise DomainError(
            "ERR_RETENTION", "Name the categories retained under the cited policy.", 422
        )
    if any(not c.strip() or len(c) > 160 for c in data.retained_categories):
        raise DomainError("ERR_RETENTION", "Retained categories must be nonempty and bounded.", 422)
    count = len(
        list(
            db.scalars(
                select(PrivacyReview.id)
                .where(PrivacyReview.business_id == bid, PrivacyReview.request_id == rid)
                .limit(21)
            )
        )
    )
    if count >= 20:
        raise DomainError("ERR_QUOTA", "Privacy decision history reached its local cap.", 413)
    decision = PrivacyReview(
        business_id=bid, request_id=rid, reviewed_by=who.user.id, **data.model_dump()
    )
    db.add(decision)
    db.flush()
    record(db, who.user.id, "privacy.reviewed", bid, decision.id)
    return {
        "id": str(decision.id),
        "request_id": str(rid),
        "status": data.outcome,
        "deleted": False,
        "retained_categories": data.retained_categories,
        "policy_reference": data.policy_reference,
        "export_path": f"/api/v1/businesses/{bid}/privacy-export"
        if data.outcome == "export_ready"
        else None,
        "next_action": "Requester downloads with their own MFA session."
        if data.outcome == "export_ready"
        else "Follow the recorded retention/follow-up decision.",
    }


def history(db: Session, who: Principal, bid: UUID, rid: UUID) -> dict[str, Any]:
    member = authorize(db, who, bid)
    item = db.scalar(
        select(PrivacyRequest).where(PrivacyRequest.business_id == bid, PrivacyRequest.id == rid)
    )
    if not item or (item.user_id != who.user.id and member.role not in {"owner", "accountant"}):
        raise DomainError("ERR_NOT_FOUND", "Privacy request not found.", 404)
    rows = list(
        db.scalars(
            select(PrivacyReview)
            .where(PrivacyReview.business_id == bid, PrivacyReview.request_id == rid)
            .order_by(PrivacyReview.created_at, PrivacyReview.id)
            .limit(20)
        )
    )
    return {
        "id": str(rid),
        "kind": item.kind,
        "requester_id": str(item.user_id),
        "status": rows[-1].outcome if rows else item.status,
        "deleted": False,
        "decisions": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "outcome",
                    "reason",
                    "policy_reference",
                    "retained_categories",
                    "retain_until",
                    "legal_hold",
                    "reviewed_by",
                    "created_at",
                )
            }
            for r in rows
        ],
    }
