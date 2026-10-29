"""Private scanned evidence with explicit manual/unsupported extraction outcomes."""

import re
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Query, Request, Response
from pydantic import Field, model_validator
from sqlalchemy import event, func, select

from ledgerpilot.accounting.models import Journal
from ledgerpilot.audit.service import record
from ledgerpilot.banking.models import BankRow
from ledgerpilot.documents import duplicates, storage
from ledgerpilot.documents.duplicates import IdentityReview
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import TradeDocument
from ledgerpilot.workspace.models import Evidence, EvidenceJob, EvidenceLink, EvidenceNote
from ledgerpilot.workspace.service import evidence

router = APIRouter(prefix="/businesses/{business_id}", tags=["evidence"])


@router.get(
    "/evidence/{evidence_id}/identity",
    response_model=contracts.obj(
        "DocumentIdentityRead",
        id=UUID,
        evidence_id=UUID,
        supplier=str,
        normalized_supplier=str,
        document_date=date,
        total=str,
        currency=str,
        version=int,
        reason=str,
        reviewed_by=UUID,
    ),
)
def reviewed_identity(
    business_id: UUID, evidence_id: UUID, identity: Identity, db: DB
) -> dict[str, Any]:
    from ledgerpilot.workspace.models import DocumentIdentity

    authorize(db, identity, business_id)
    evidence(db, business_id, evidence_id)
    row = db.scalar(
        select(DocumentIdentity)
        .where(
            DocumentIdentity.business_id == business_id, DocumentIdentity.evidence_id == evidence_id
        )
        .order_by(DocumentIdentity.version.desc())
        .limit(1)
    )
    if row is None:
        raise DomainError("ERR_NOT_FOUND", "Document identity has not been reviewed.", 404)
    return {
        **{
            k: getattr(row, k)
            for k in (
                "id",
                "evidence_id",
                "supplier",
                "normalized_supplier",
                "document_date",
                "currency",
                "version",
                "reason",
                "reviewed_by",
            )
        },
        "total": str(row.total),
    }


@router.post(
    "/evidence/{evidence_id}/identity",
    status_code=201,
    response_model=contracts.responses["documents.identity_review"],
    response_model_exclude_unset=True,
)
def identity_review(
    business_id: UUID, evidence_id: UUID, data: "IdentityReview", identity: Identity, db: DB
) -> dict[str, Any]:
    return duplicates.review(db, identity, business_id, evidence_id, data)


@router.get(
    "/evidence/{evidence_id}/duplicate-candidates",
    response_model=contracts.responses["documents.duplicate_candidates"],
    response_model_exclude_unset=True,
)
def duplicate_candidates(
    business_id: UUID, evidence_id: UUID, identity: Identity, db: DB
) -> dict[str, Any]:
    return duplicates.candidates(db, identity, business_id, evidence_id)


@router.get(
    "/evidence/capabilities",
    response_model=contracts.responses["documents.capabilities"],
    response_model_exclude_unset=True,
)
def capabilities(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return {
        "ocr_configured": request.app.state.settings.ocr_enabled,
        "automatic_types": ["application/pdf", "image/png", "image/jpeg"],
        "ocr_model_language": "eng",
        "pdf_text_first": True,
        "max_pages": 10,
        "max_image_pixels": 16000000,
        "max_bytes": request.app.state.settings.evidence_max_bytes,
        "confidence_is_calibrated": False,
        "semantic_assignment": "manual",
        "human_review_required": True,
        "automatic_posting": False,
    }


@router.get(
    "/evidence/{evidence_id}",
    response_model=contracts.responses["documents.evidence_detail"],
    response_model_exclude_unset=True,
)
def evidence_detail(
    business_id: UUID,
    evidence_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    item = evidence(db, business_id, evidence_id)
    return {
        k: getattr(item, k)
        for k in (
            "id",
            "filename",
            "content_hash",
            "media_type",
            "size",
            "status",
            "scan_version",
            "raw_text",
            "extraction",
        )
    }


class FieldRegion(Schema):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=1000)
    value_type: Literal["text", "date", "amount"]
    page: int = Field(ge=1, le=1000)
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_region(self) -> "FieldRegion":
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Field region must have positive area")
        if self.value_type == "amount" and not re.fullmatch(
            r"-?[0-9]+(?:\.[0-9]{1,6})?", self.value
        ):
            raise ValueError("Amount fields use exact decimal strings")
        if self.value_type == "date":
            from datetime import date

            date.fromisoformat(self.value)
        return self


class NoteCreate(Schema):
    note: str = Field(default="", max_length=2000)
    fields: list[FieldRegion] = Field(default_factory=list, max_length=100)


class LinkCreate(Schema):
    resource_kind: Literal["trade_document", "bank_transaction", "journal"]
    resource_id: UUID


@router.post(
    "/evidence/upload",
    status_code=201,
    response_model=contracts.responses["documents.upload"],
    response_model_exclude_unset=True,
)
def upload(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    filename: str = Query(min_length=1, max_length=160, pattern=r"^[^/\\\r\n]+$"),
    content: bytes = Body(media_type="application/octet-stream"),
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
    get_business(db, business_id, lock=True)
    settings = request.app.state.settings
    from ledgerpilot.workspace.models import TrialEntitlement

    trial = db.scalar(select(TrialEntitlement).where(TrialEntitlement.business_id == business_id))
    maximum = min(settings.evidence_max_bytes, trial.document_byte_limit if trial else 26214400)
    count = (
        db.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.business_id == business_id)
        )
        or 0
    )
    if count >= 200:
        raise DomainError(
            "ERR_QUOTA", "The pilot supports 200 retained evidence originals per business.", 413
        )
    stored = (
        db.scalar(
            select(func.coalesce(func.sum(Evidence.size), 0)).where(
                Evidence.business_id == business_id,
            )
        )
        or 0
    )
    if stored + len(content) > 100 * 1024 * 1024:
        raise DomainError("ERR_QUOTA", "The pilot evidence quota is 100 MiB per business.", 413)
    if not content or len(content) > maximum:
        raise DomainError("ERR_FILE_SIZE", "Upload is empty or exceeds the configured cap.", 413)
    storage.cipher(settings)  # Refuse before scan/storage if key configuration is incomplete.
    media_type = storage.detect(content, filename)
    eid = uuid4()
    key, digest = storage.put(settings, business_id, eid, content)

    def cleanup(*_: object) -> None:
        storage.path(settings, key).unlink(missing_ok=True)

    event.listen(db, "after_rollback", cleanup, once=True)
    duplicates = list(
        db.scalars(
            select(Evidence.id)
            .where(
                Evidence.business_id == business_id,
                Evidence.content_hash == digest,
            )
            .limit(100)
        )
    )
    item = Evidence(
        id=eid,
        business_id=business_id,
        filename=filename,
        content_hash=digest,
        media_type=media_type,
        size=len(content),
        status="quarantined",
        scan_version=None,
        object_key=key,
        raw_text=None,
        extraction={
            "status": "queued",
            "reason": "Awaiting malware scanning by the scoped local worker.",
            "fields": [],
            "model_version": None,
        },
        created_by=identity.user.id,
    )
    db.add(item)
    db.flush()
    job = EvidenceJob(business_id=business_id, evidence_id=eid, created_by=identity.user.id)
    db.add(job)
    db.flush()
    record(db, identity.user.id, "evidence.uploaded", business_id, eid)
    return {
        "id": str(eid),
        "content_hash": digest,
        "scan_status": "quarantined",
        "job_id": str(job.id),
        "extraction": item.extraction,
        "duplicate_candidates": [str(d) for d in duplicates],
        "manual_review_available": False,
        "posted": False,
    }


@router.get(
    "/evidence",
    response_model=contracts.responses["documents.list_evidence"],
    response_model_exclude_unset=True,
)
def list_evidence(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(Evidence).where(Evidence.business_id == business_id)
    if cursor:
        query = query.where(Evidence.id > cursor)
    rows = list(db.scalars(query.order_by(Evidence.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "filename",
                    "content_hash",
                    "media_type",
                    "size",
                    "status",
                    "extraction",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/evidence/{evidence_id}/download",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
def download(
    business_id: UUID,
    evidence_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
) -> Response:
    authorize(db, identity, business_id)
    item = evidence(db, business_id, evidence_id)
    content = storage.get(request.app.state.settings, item.object_key, item.content_hash)
    record(db, identity.user.id, "evidence.downloaded", business_id, evidence_id)
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="evidence-{evidence_id}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/evidence/{evidence_id}/notes",
    status_code=201,
    response_model=contracts.responses["documents.note"],
    response_model_exclude_unset=True,
)
def note(
    business_id: UUID,
    evidence_id: UUID,
    data: NoteCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
    evidence(db, business_id, evidence_id)
    item = EvidenceNote(
        business_id=business_id,
        evidence_id=evidence_id,
        note=data.note,
        fields=[
            {**f.model_dump(), "confidence": None, "source": "manual_review", "model_version": None}
            for f in data.fields
        ],
        created_by=identity.user.id,
    )
    db.add(item)
    db.flush()
    record(db, identity.user.id, "evidence.annotated", business_id, evidence_id)
    return {"id": str(item.id), "fields": item.fields, "note": item.note}


@router.get(
    "/evidence/{evidence_id}/notes",
    response_model=contracts.responses["documents.notes"],
    response_model_exclude_unset=True,
)
def notes(
    business_id: UUID,
    evidence_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    evidence(db, business_id, evidence_id)
    query = select(EvidenceNote).where(
        EvidenceNote.business_id == business_id, EvidenceNote.evidence_id == evidence_id
    )
    if cursor:
        query = query.where(EvidenceNote.id > cursor)
    rows = list(db.scalars(query.order_by(EvidenceNote.id).limit(limit + 1)))
    return {
        "items": [
            {k: getattr(r, k) for k in ("id", "note", "fields", "created_by", "created_at")}
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post(
    "/evidence/{evidence_id}/links",
    status_code=201,
    response_model=contracts.responses["documents.link"],
    response_model_exclude_unset=True,
)
def link(
    business_id: UUID,
    evidence_id: UUID,
    data: LinkCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
    evidence(db, business_id, evidence_id)
    registry: dict[str, type[TradeDocument] | type[BankRow] | type[Journal]] = {
        "trade_document": TradeDocument,
        "bank_transaction": BankRow,
        "journal": Journal,
    }
    model = registry[data.resource_kind]
    if (
        db.scalar(
            select(model.id).where(model.business_id == business_id, model.id == data.resource_id)
        )
        is None
    ):
        raise DomainError("ERR_NOT_FOUND", "Linked resource not found.", 404)
    old = db.scalar(
        select(EvidenceLink).where(
            EvidenceLink.business_id == business_id,
            EvidenceLink.evidence_id == evidence_id,
            EvidenceLink.resource_kind == data.resource_kind,
            EvidenceLink.resource_id == data.resource_id,
        )
    )
    if old:
        return {"id": str(old.id)}
    item = EvidenceLink(business_id=business_id, evidence_id=evidence_id, **data.model_dump())
    db.add(item)
    db.flush()
    record(db, identity.user.id, "evidence.linked", business_id, evidence_id)
    return {"id": str(item.id)}
