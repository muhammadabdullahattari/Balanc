"""Durable encrypted export snapshots, authenticated resumable HTTP byte ranges."""

import hashlib
import re
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.documents.storage import cipher
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.identity.security import now
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.operations import fingerprint
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.workspace import exports, service
from ledgerpilot.workspace.models import ExportSnapshot


class ExportCreate(Schema):
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    format: Literal["journals_csv", "evidence_zip"]
    start: date
    end: date


def metadata(item: ExportSnapshot) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "format": item.format,
        "size": item.size,
        "sha256": item.digest,
        "expires_at": item.expires_at,
        "business_version": item.business_version,
        "state": "ready",
        "range_chunk_limit": 1048576,
    }


def create(
    db: Session, who: Principal, bid: UUID, data: ExportCreate, settings: Settings
) -> dict[str, Any]:
    authorize(db, who, bid)
    business = get_business(db, bid, lock=True)
    if data.end < data.start:
        raise DomainError("ERR_DATE_RANGE", "Export dates must be ordered.", 422)
    db.execute(
        delete(ExportSnapshot).where(
            ExportSnapshot.business_id == bid,
            ExportSnapshot.created_by == who.user.id,
            ExportSnapshot.expires_at <= now(),
        )
    )
    old = db.scalar(
        select(ExportSnapshot).where(
            ExportSnapshot.business_id == bid,
            ExportSnapshot.created_by == who.user.id,
            ExportSnapshot.key == data.idempotency_key,
        )
    )
    request_hash = fingerprint(data, "export.snapshot")
    if old:
        if old.request_hash != request_hash:
            raise DomainError("ERR_IDEMPOTENCY_CONFLICT", "Export key was already used.", 409)
        return metadata(old)
    count = (
        db.scalar(
            select(func.count())
            .select_from(ExportSnapshot)
            .where(
                ExportSnapshot.business_id == bid,
                ExportSnapshot.created_by == who.user.id,
            )
        )
        or 0
    )
    if count >= 3:
        raise DomainError("ERR_QUOTA", "At most three retained exports per member/business.", 413)
    body = (
        service.journal_csv(db, bid, data.start, data.end).encode()
        if data.format == "journals_csv"
        else (exports.pack(db, settings, bid, data.start, data.end, who))
    )
    if not body or len(body) > 10 * 1024 * 1024:
        raise DomainError("ERR_QUOTA", "Narrow the export to at most 10 MiB.", 413)
    item = ExportSnapshot(
        business_id=bid,
        key=data.idempotency_key,
        request_hash=request_hash,
        format=data.format,
        created_by=who.user.id,
        business_version=business.version,
        digest=hashlib.sha256(body).hexdigest(),
        size=len(body),
        payload=cipher(settings).encrypt(body),
        expires_at=now() + timedelta(hours=24),
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "export.snapshot_created", bid, item.id)
    return metadata(item)


def owned(db: Session, who: Principal, bid: UUID, sid: UUID) -> ExportSnapshot:
    authorize(db, who, bid)
    item = db.scalar(
        select(ExportSnapshot).where(
            ExportSnapshot.business_id == bid,
            ExportSnapshot.id == sid,
            ExportSnapshot.created_by == who.user.id,
        )
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Export not found.", 404)
    if item.expires_at <= now():
        raise DomainError("ERR_EXPORT_EXPIRED", "Export expired; request a new snapshot.", 410)
    return item


def download(
    item: ExportSnapshot, settings: Settings, header: str | None, if_match: str | None
) -> tuple[bytes, int, dict[str, str]]:
    etag = '"' + item.digest + '"'
    if if_match is not None and if_match != etag:
        raise DomainError("ERR_EXPORT_VERSION", "Export checksum does not match.", 412)
    try:
        body = cipher(settings).decrypt(item.payload)
    except InvalidToken:
        raise DomainError("ERR_INTEGRITY", "Export could not be verified.", 503) from None
    if len(body) != item.size or hashlib.sha256(body).hexdigest() != item.digest:
        raise DomainError("ERR_INTEGRITY", "Export could not be verified.", 503)
    headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": 'attachment; filename="ledgerpilot-export.'
        + ("csv" if item.format == "journals_csv" else "zip")
        + '"',
    }
    if header is None:
        return body, 200, headers
    match = re.fullmatch(r"bytes=([0-9]{1,10})-([0-9]{0,10})", header)
    if not match:
        raise DomainError("ERR_RANGE", "Use one explicit bytes=start-end range.", 416)
    start, end = (
        int(match[1]),
        int(match[2]) if match[2] else min(item.size - 1, int(match[1]) + 1048575),
    )
    if start >= item.size or end < start or end >= item.size or end - start + 1 > 1048576:
        raise DomainError("ERR_RANGE", "Range is outside the export or exceeds 1 MiB.", 416)
    headers["Content-Range"] = f"bytes {start}-{end}/{item.size}"
    return body[start : end + 1], 206, headers
