"""Private scanned evidence with explicit manual/unsupported extraction outcomes."""

import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Response
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
    openapi_extra={
        "requestBody": {
            "content": {
                "application/octet-stream": {
                    "schema": {
                        "type": "string",
                        "contentMediaType": "application/octet-stream",
                        "title": "Content",
                    }
                }
            },
            "required": True,
        }
    },
)
async def upload(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    filename: str = Query(min_length=1, max_length=160, pattern=r"^[^/\\\r\n]+$"),
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
    # Do not hold exclusive workspace row lock across incoming network upload stream
    get_business(db, business_id, lock=False)
    settings = request.app.state.settings
    from ledgerpilot.workspace.models import TrialEntitlement

    trial = db.scalar(select(TrialEntitlement).where(TrialEntitlement.business_id == business_id))
    maximum = min(settings.evidence_max_bytes, trial.document_byte_limit if trial else 26214400)

    # Reverse proxy / boundary check: fast reject based on Content-Length header
    content_length_header = request.headers.get("content-length")
    if content_length_header is not None:
        try:
            cl = int(content_length_header)
            if cl > maximum:
                raise DomainError(
                    "ERR_FILE_SIZE", "Upload is empty or exceeds the configured cap.", 413
                )
            if cl == 0:
                raise DomainError(
                    "ERR_FILE_SIZE", "Upload is empty or exceeds the configured cap.", 413
                )
        except ValueError:
            pass

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
    if content_length_header is not None:
        try:
            cl = int(content_length_header)
            if stored + cl > 100 * 1024 * 1024:
                raise DomainError(
                    "ERR_QUOTA", "The pilot evidence quota is 100 MiB per business.", 413
                )
        except ValueError:
            pass

    storage.cipher(settings)  # Refuse before scan/storage if key configuration is incomplete.

    # Read first chunk to detect file type without buffering entire file in memory
    stream = request.stream()
    first_chunk = b""
    async for chunk in stream:
        if chunk:
            first_chunk = chunk
            break

    if not first_chunk:
        raise DomainError("ERR_FILE_SIZE", "Upload is empty or exceeds the configured cap.", 413)

    media_type = storage.detect(first_chunk, filename)
    eid = uuid4()

    async def combined_stream() -> AsyncIterator[bytes]:
        yield first_chunk
        async for next_chunk in stream:
            if next_chunk:
                yield next_chunk

    key, digest, size = await storage.put_stream_async(
        settings, business_id, eid, combined_stream(), max_bytes=maximum
    )

    if stored + size > 100 * 1024 * 1024:
        storage.delete(settings, key)
        raise DomainError("ERR_QUOTA", "The pilot evidence quota is 100 MiB per business.", 413)

    def cleanup(*_: object) -> None:
        storage.delete(settings, key)

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
        size=size,
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


@router.post("/evidence/{evidence_id}/understand")
def understand_evidence_document(
    business_id: UUID,
    evidence_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    channel: str = Query("upload", max_length=40),
) -> dict[str, Any]:
    """Run structured field extraction, validation, and cross-channel deduplication."""
    from ledgerpilot.documents.understanding import (
        claim_document_fingerprint,
        extract_structured_document,
    )
    from ledgerpilot.tenancy.service import get_business

    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    item = evidence(db, business_id, evidence_id)
    ext_dict = item.extraction if isinstance(item.extraction, dict) else {}
    raw_text = str(ext_dict.get("raw_text") or item.filename)
    ocr_words = ext_dict.get("fields") if isinstance(ext_dict.get("fields"), list) else []

    extracted = extract_structured_document(
        raw_text,
        filename=item.filename,
        ocr_words=ocr_words,
        default_currency=biz.currency,
        settings=request.app.state.settings,
    )
    try:
        raw_bytes = storage.get(request.app.state.settings, item.object_key, item.content_hash)
    except Exception:
        raw_bytes = item.content_hash.encode("utf-8")

    claim = claim_document_fingerprint(
        db,
        business_id=business_id,
        actor_id=identity.user.id,
        content_bytes=raw_bytes,
        channel=channel,
        extracted=extracted,
        evidence_id=item.id,
        create_draft=True,
    )
    return {"evidence_id": str(item.id), "extraction": extracted, "deduplication": claim}


@router.post("/documents/ingest", status_code=201)
def ingest_channel_document(
    business_id: UUID,
    body: dict[str, Any],
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Unified multi-channel document intake (mobile capture, portal, email, drive, upload)."""
    import base64

    from ledgerpilot.documents.understanding import (
        claim_document_fingerprint,
        extract_structured_document,
    )
    from ledgerpilot.tenancy.service import get_business

    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    channel = str(body.get("channel") or "upload").strip()[:40]
    filename = str(body.get("filename") or "captured-receipt.jpg").strip()[:200]
    raw_text = str(body.get("raw_text") or "").strip()
    b64 = str(body.get("content_base64") or "").strip()
    content_bytes = (
        base64.b64decode(b64)
        if b64
        else (raw_text.encode("utf-8") if raw_text else filename.encode("utf-8"))
    )

    extracted = extract_structured_document(
        raw_text or filename,
        filename=filename,
        default_currency=biz.currency,
        settings=request.app.state.settings,
    )
    claim = claim_document_fingerprint(
        db,
        business_id=business_id,
        actor_id=identity.user.id,
        content_bytes=content_bytes,
        channel=channel,
        extracted=extracted,
        evidence_id=None,
        create_draft=bool(body.get("create_draft", True)),
    )
    return {
        "channel": channel,
        "filename": filename,
        "extraction": extracted,
        "deduplication": claim,
    }


@router.get("/documents/fingerprints")
def list_document_fingerprints(
    business_id: UUID,
    identity: Identity,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List cross-channel deduplicated documents and their intake channels."""
    from sqlalchemy import text

    authorize(db, identity, business_id)
    rows = (
        db.execute(
            text(
                """
                SELECT id, content_sha256, doc_type, vendor_name, doc_number,
                       doc_date, currency, total_amount, first_evidence_id,
                       source_draft_id, channels, overall_confidence, created_at
                FROM app.document_fingerprints
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"bid": business_id, "lim": limit},
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "id": str(r["id"]),
                "content_sha256": str(r["content_sha256"]),
                "doc_type": str(r["doc_type"]),
                "vendor_name": str(r["vendor_name"]),
                "doc_number": str(r["doc_number"]),
                "doc_date": r["doc_date"].isoformat() if r["doc_date"] else None,
                "currency": str(r["currency"]),
                "total_amount": f"{r['total_amount']:.2f}"
                if r["total_amount"] is not None
                else "0.00",
                "first_evidence_id": str(r["first_evidence_id"])
                if r["first_evidence_id"]
                else None,
                "source_draft_id": str(r["source_draft_id"]) if r["source_draft_id"] else None,
                "channels": list(r["channels"] or []),
                "overall_confidence": str(r["overall_confidence"]),
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
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
    "/evidence/{evidence_id}/preview",
    response_class=Response,
    responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}},
)
def preview_evidence_page(
    business_id: UUID,
    evidence_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    page: int = Query(1, ge=1, le=25),
) -> Response:
    """Render a same-origin visual preview image for an evidence page."""
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    authorize(db, identity, business_id)
    item = evidence(db, business_id, evidence_id)
    content_bytes: bytes | None = None
    try:
        content_bytes = storage.get(request.app.state.settings, item.object_key, item.content_hash)
    except Exception:
        content_bytes = None

    if content_bytes and item.media_type in {"image/png", "image/jpeg"} and page == 1:
        return Response(
            content_bytes,
            media_type=item.media_type,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    extraction = item.extraction if isinstance(item.extraction, dict) else {}
    raw_fields = extraction.get("fields")
    fields: list[Any] = raw_fields if isinstance(raw_fields, list) else []
    raw_text = str(extraction.get("raw_text") or "")

    fig, ax = plt.subplots(figsize=(6.8, 8.8), dpi=130)
    fig.patch.set_facecolor("#fefdfb")
    ax.set_facecolor("#ffffff")
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#dce1e8")

    page_fields = [f for f in fields if isinstance(f, dict) and int(f.get("page") or 1) == page]
    if page_fields:
        for f in page_fields[:250]:
            left = float(f.get("left") or 0.05)
            top = float(f.get("top") or 0.05)
            val = str(f.get("value") or "")[:60]
            if val:
                ax.text(left, top + 0.015, val, fontsize=7.5, color="#172438", clip_on=True)
    elif raw_text:
        lines = raw_text.splitlines()[:42]
        for idx, line in enumerate(lines):
            ax.text(0.05, 0.06 + idx * 0.021, line[:88], fontsize=8, color="#172438", clip_on=True)
    else:
        ax.text(
            0.5,
            0.5,
            f"{item.filename}\nPage {page} preview",
            ha="center",
            va="center",
            fontsize=10,
            color="#526079",
        )

    buf = io.BytesIO()
    fig.tight_layout(pad=0.3)
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return Response(
        buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


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
    from ledgerpilot.workspace.cleanup_models import CleanupPolicy, CleanupRequest

    disposed = db.scalar(
        select(CleanupRequest.id)
        .join(CleanupPolicy, CleanupPolicy.id == CleanupRequest.policy_id)
        .where(
            CleanupRequest.business_id == business_id,
            CleanupPolicy.business_id == business_id,
            CleanupPolicy.location == "local_evidence",
            CleanupRequest.resource_id == evidence_id,
            CleanupRequest.status == "verified",
        )
        .limit(1)
    )
    if disposed is not None:
        raise DomainError(
            "ERR_DISPOSED",
            "This document payload was removed under an approved retention policy.",
            410,
        )
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
