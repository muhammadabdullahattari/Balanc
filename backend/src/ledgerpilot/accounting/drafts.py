"""Durable unposted work with optimistic versioning and a single posting transition."""

import json
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ledgerpilot.accounting import repository, service
from ledgerpilot.accounting.models import Journal, JournalLine
from ledgerpilot.accounting.schemas import (
    DraftCreate,
    DraftPublish,
    DraftUpdate,
    JournalCreate,
    LineCreate,
)
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.identity.security import digest
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize, get_business


def add_lines(db: Session, bid: UUID, jid: UUID, data: DraftCreate) -> None:
    for position, line in enumerate(data.lines, 1):
        db.add(
            JournalLine(
                business_id=bid,
                journal_id=jid,
                account_id=line.account_id,
                position=position,
                debit=Decimal(line.debit),
                credit=Decimal(line.credit),
            )
        )


def create(db: Session, identity: Principal, bid: UUID, data: DraftCreate) -> Journal:
    authorize(db, identity, bid, {"owner", "accountant", "bookkeeper"})
    get_business(db, bid, lock=True)
    jid = uuid4()
    item = Journal(
        id=jid,
        business_id=bid,
        accounting_date=data.accounting_date,
        memo=data.memo,
        currency=data.currency,
        idempotency_key="draft-" + jid.hex,
        request_hash=digest(json.dumps(data.model_dump(mode="json"), sort_keys=True)),
        created_by=identity.user.id,
        status="draft",
        version=0,
        source_kind="manual_journal",
        source_id=jid,
    )
    db.add(item)
    db.flush()
    add_lines(db, bid, jid, data)
    record(db, identity.user.id, "journal.draft_created", bid, jid)
    return item


def update(db: Session, identity: Principal, bid: UUID, jid: UUID, data: DraftUpdate) -> Journal:
    authorize(db, identity, bid, {"owner", "accountant", "bookkeeper"})
    get_business(db, bid, lock=True)
    item = repository.journal(db, bid, jid)
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    if item.status != "draft" or item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Draft changed or was already posted.", 409)
    item.accounting_date, item.memo, item.currency = data.accounting_date, data.memo, data.currency
    item.version += 1
    db.execute(
        delete(JournalLine).where(JournalLine.business_id == bid, JournalLine.journal_id == jid)
    )
    add_lines(db, bid, jid, data)
    record(db, identity.user.id, "journal.draft_updated", bid, jid)
    return item


def publish(db: Session, identity: Principal, bid: UUID, jid: UUID, data: DraftPublish) -> UUID:
    authorize(db, identity, bid, posting=True)
    get_business(db, bid, lock=True)
    item = repository.journal(db, bid, jid)
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    lines = repository.lines(db, bid, jid)
    if len(lines) < 2:
        raise DomainError("ERR_BALANCE", "At least two balanced journal lines are required.")
    request = JournalCreate(
        accounting_date=item.accounting_date,
        memo=item.memo,
        currency=item.currency,
        idempotency_key=data.idempotency_key,
        expected_version=data.expected_business_version,
        lines=[
            LineCreate(
                account_id=line.account_id,
                debit=format(line.debit, "f"),
                credit=format(line.credit, "f"),
            )
            for line in lines
        ],
    )
    return service.post(
        db, identity, bid, request, draft_id=jid, draft_version=data.expected_version
    )
