"""Sales and bill preparation stays separate from authorized financial posting."""

from typing import Any
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.operations import fingerprint
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import SourceDraft
from ledgerpilot.trade.schemas import DocumentCreate
from ledgerpilot.trade.service import create_document, document_impact


class DraftEdit(Schema):
    expected_version: int = Field(ge=0)
    payload: DocumentCreate


class DraftAction(Schema):
    expected_version: int = Field(ge=0)
    expected_business_version: int = Field(ge=0)


def read(item: SourceDraft) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "payload": item.payload,
        "status": item.status,
        "version": item.version,
        "document_id": str(item.document_id) if item.document_id else None,
        "prepared_by": str(item.prepared_by),
    }


def get(db: Session, bid: UUID, did: UUID) -> SourceDraft:
    item = db.scalar(
        select(SourceDraft).where(SourceDraft.business_id == bid, SourceDraft.id == did)
    )
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Source draft not found.", 404)
    return item


def create(db: Session, who: Principal, bid: UUID, data: DocumentCreate) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    business = get_business(db, bid, lock=True)
    previous = db.scalar(
        select(SourceDraft).where(
            SourceDraft.business_id == bid, SourceDraft.key == data.idempotency_key
        )
    )
    if previous is not None:
        old = DocumentCreate.model_validate(previous.payload)
        if fingerprint(old, "source.draft") != fingerprint(data, "source.draft"):
            raise DomainError("ERR_IDEMPOTENCY_CONFLICT", "Draft key was already used.", 409)
        return read(previous)
    document_impact(db, business, data)
    item = SourceDraft(
        business_id=bid,
        key=data.idempotency_key,
        payload=data.model_dump(mode="json"),
        prepared_by=who.user.id,
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "source.drafted", bid, item.id)
    return read(item)


def update(db: Session, who: Principal, bid: UUID, did: UUID, data: DraftEdit) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    business = get_business(db, bid, lock=True)
    item = get(db, bid, did)
    if item.status != "draft" or item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Draft changed or is no longer editable.", 409)
    if data.payload.idempotency_key != item.key:
        raise DomainError("ERR_DRAFT_KEY", "The source operation key cannot change.")
    document_impact(db, business, data.payload)
    item.payload, item.version = data.payload.model_dump(mode="json"), item.version + 1
    record(db, who.user.id, "source.draft_updated", bid, did)
    return read(item)


def publish(
    db: Session,
    who: Principal,
    bid: UUID,
    did: UUID,
    data: DraftAction,
) -> dict[str, Any]:
    authorize(db, who, bid, posting=True)
    get_business(db, bid, lock=True)
    item = get(db, bid, did)
    if item.status == "issued":
        return {"id": str(item.document_id), "draft_id": str(item.id), "status": "issued"}
    if item.status != "draft" or item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Draft changed or was voided.", 409)
    payload = DocumentCreate.model_validate(
        {**item.payload, "expected_version": data.expected_business_version}
    )
    posted = create_document(db, who, bid, payload)
    item.status, item.document_id = "issued", UUID(str(posted["id"]))
    item.version += 1
    record(db, who.user.id, "source.draft_issued", bid, did)
    return {"id": str(item.document_id), "draft_id": str(item.id), "status": "issued"}


def void(db: Session, who: Principal, bid: UUID, did: UUID, data: DraftAction) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    get_business(db, bid, lock=True)
    item = get(db, bid, did)
    if item.status != "draft" or item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Only a current draft may be voided.", 409)
    item.status, item.version = "void", item.version + 1
    record(db, who.user.id, "source.draft_voided", bid, did)
    return read(item)
