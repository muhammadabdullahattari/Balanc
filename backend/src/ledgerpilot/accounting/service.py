"""Posting enters PostgreSQL through one authorized, atomic gateway."""

import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ledgerpilot.accounting import repository
from ledgerpilot.accounting.schemas import JournalCreate, LineCreate, ReversalCreate
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize


def round_money(value: Decimal, precision: int) -> Decimal:
    if not value.is_finite() or not 0 <= precision <= 6:
        raise DomainError("ERR_AMOUNT", "Unsupported monetary amount.")
    return value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_EVEN)


def post(
    db: Session,
    identity: Principal,
    bid: UUID,
    data: JournalCreate,
    reversal_of: UUID | None = None,
    draft_id: UUID | None = None,
    draft_version: int | None = None,
) -> UUID:
    authorize(db, identity, bid, posting=True)
    if sum((Decimal(line.debit) - Decimal(line.credit) for line in data.lines), Decimal(0)):
        raise DomainError("ERR_BALANCE", "Journal debits and credits must balance.")
    payload = data.model_dump(mode="json", exclude={"expected_version"})
    payload["reversal_of"] = str(reversal_of) if reversal_of else None
    payload["draft_id"] = str(draft_id) if draft_id else None
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        gateway = (
            (
                "SELECT app.post_draft_journal(:b,:date,:memo,:currency,:key,:hash,:version,"
                "CAST(:lines AS jsonb),:reversal,:draft,:draft_version)"
            )
            if draft_id
            else (
                "SELECT app.post_journal(:b,:date,:memo,:currency,:key,:hash,:version,"
                "CAST(:lines AS jsonb),:reversal)"
            )
        )
        result: UUID = db.execute(
            text(gateway),
            {
                "b": bid,
                "date": data.accounting_date,
                "memo": data.memo,
                "currency": data.currency,
                "key": data.idempotency_key,
                "hash": fingerprint,
                "version": data.expected_version,
                "lines": json.dumps([line.model_dump(mode="json") for line in data.lines]),
                "reversal": reversal_of,
                "draft": draft_id,
                "draft_version": draft_version,
            },
        ).scalar_one()
        return result
    except DBAPIError as exc:
        message = getattr(getattr(exc.orig, "diag", None), "message_primary", "")
        known = {"ERR_VERSION", "ERR_IDEMPOTENCY_CONFLICT", "ERR_PERIOD_LOCKED"}
        if message in known:
            raise DomainError(message, "Conflict with the current accounting state.", 409) from None
        raise


def reverse(db: Session, identity: Principal, bid: UUID, jid: UUID, data: ReversalCreate) -> UUID:
    authorize(db, identity, bid, posting=True)
    original = repository.journal(db, bid, jid)
    if not original or original.reversal_of or original.status != "posted":
        raise DomainError("ERR_NOT_FOUND", "Reversible journal not found.", 404)
    if data.accounting_date < original.accounting_date:
        raise DomainError(
            "ERR_REVERSAL_DATE", "A reversal cannot predate its original journal.", 409
        )
    lines = repository.lines(db, bid, jid)
    request = JournalCreate(
        accounting_date=data.accounting_date,
        memo=data.memo,
        currency=original.currency,
        idempotency_key=data.idempotency_key,
        expected_version=data.expected_version,
        lines=[
            LineCreate(
                account_id=line.account_id,
                debit=format(line.credit, "f"),
                credit=format(line.debit, "f"),
            )
            for line in lines
        ],
    )
    return post(db, identity, bid, request, jid)
