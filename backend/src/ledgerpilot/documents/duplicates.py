"""Human-reviewed supplier/date/total identity; candidates never merge originals."""

import re
import unicodedata
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.schemas import exact_amount
from ledgerpilot.workspace.models import DocumentIdentity, Evidence
from ledgerpilot.workspace.service import evidence


class IdentityReview(Schema):
    expected_version: int = Field(ge=0)
    supplier: str = Field(min_length=1, max_length=160)
    document_date: date
    total: str
    currency: str = Field(min_length=3, max_length=3)
    reason: str = Field(min_length=1, max_length=1000)
    _money = field_validator("total")(exact_amount)


def normalize(value: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def review(
    db: Session, who: Principal, bid: UUID, eid: UUID, data: IdentityReview
) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper", "reviewer"})
    business = get_business(db, bid, lock=True)
    evidence(db, bid, eid)
    latest = db.scalar(
        select(DocumentIdentity)
        .where(DocumentIdentity.business_id == bid, DocumentIdentity.evidence_id == eid)
        .order_by(DocumentIdentity.version.desc())
        .limit(1)
    )
    if data.expected_version != (latest.version if latest else 0):
        raise DomainError("ERR_VERSION", "Identity changed; review the latest version.", 409)
    if data.expected_version >= 100:
        raise DomainError("ERR_QUOTA", "Identity review history has reached its local cap.", 413)
    supplier = normalize(data.supplier)
    if not supplier or len(supplier) > 320 or data.currency != business.currency:
        raise DomainError("ERR_IDENTITY", "Supply a supplier and the business currency.", 422)
    amount = ops.money(data.total, business)
    item = DocumentIdentity(
        business_id=bid,
        evidence_id=eid,
        supplier=data.supplier,
        normalized_supplier=supplier,
        document_date=data.document_date,
        total=amount,
        currency=data.currency,
        version=data.expected_version + 1,
        reason=data.reason,
        reviewed_by=who.user.id,
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "evidence.identity_reviewed", bid, eid)
    return {
        "id": str(item.id),
        "evidence_id": str(eid),
        "version": item.version,
        "normalized_supplier": supplier,
        "automatic_merge": False,
    }


def candidates(db: Session, who: Principal, bid: UUID, eid: UUID) -> dict[str, Any]:
    authorize(db, who, bid)
    source = evidence(db, bid, eid)
    originals = list(
        db.scalars(
            select(Evidence)
            .where(
                Evidence.business_id == bid,
                Evidence.id != eid,
                Evidence.status.in_(["clean", "extracted", "unsupported"]),
            )
            .order_by(Evidence.id)
            .limit(200)
        )
    )
    reviews = db.scalars(
        select(DocumentIdentity)
        .where(DocumentIdentity.business_id == bid)
        .order_by(DocumentIdentity.version.desc(), DocumentIdentity.id)
    )
    latest: dict[UUID, DocumentIdentity] = {}
    for row in reviews:
        latest.setdefault(row.evidence_id, row)
    identity = latest.get(eid)
    result = []
    for other in originals:
        reasons = []
        if other.content_hash == source.content_hash:
            reasons.append("same_content_hash")
        match = latest.get(other.id)
        if (
            identity
            and match
            and (
                identity.normalized_supplier,
                identity.document_date,
                identity.currency,
                identity.total,
            )
            == (match.normalized_supplier, match.document_date, match.currency, match.total)
        ):
            reasons.append("reviewed_supplier_date_total")
        if reasons:
            result.append({"evidence_id": str(other.id), "reasons": reasons})
    return {
        "items": result,
        "requires_review": True,
        "automatic_merge": False,
        "identity_available": identity is not None,
        "abstention": None if identity else "Supplier/date/total identity is not reviewed.",
    }
