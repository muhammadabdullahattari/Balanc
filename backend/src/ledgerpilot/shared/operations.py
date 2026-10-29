"""Serialized, idempotent business operations sharing the canonical posting gateway."""

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account
from ledgerpilot.accounting.schemas import JournalCreate, LineCreate
from ledgerpilot.accounting.service import post, round_money
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.models import Business
from ledgerpilot.tenancy.service import authorize, get_business


class Command(Schema):
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_version: int = Field(ge=0)


def fingerprint(data: Schema, action: str) -> str:
    value = data.model_dump(mode="json", exclude={"expected_version"})
    return hashlib.sha256(
        json.dumps([action, value], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def begin(
    db: Session,
    identity: Principal,
    bid: UUID,
    data: Command,
    action: str,
    *,
    posting: bool = True,
) -> tuple[Business, dict[str, Any] | None]:
    from ledgerpilot.trade.models import Operation

    authorize(db, identity, bid, posting=posting)
    business = get_business(db, bid, lock=True)
    from ledgerpilot.identity.security import now
    from ledgerpilot.workspace.models import TrialEntitlement

    trial = db.scalar(select(TrialEntitlement).where(TrialEntitlement.business_id == bid))
    old = db.scalar(
        select(Operation).where(
            Operation.business_id == bid,
            Operation.key == data.idempotency_key,
        )
    )
    if old:
        if old.request_hash != fingerprint(data, action):
            raise DomainError("ERR_IDEMPOTENCY_CONFLICT", "Operation key was already used.", 409)
        return business, old.result
    count = (
        db.scalar(
            select(func.count())
            .select_from(Operation)
            .where(
                Operation.business_id == bid,
            )
        )
        or 0
    )
    if count >= 10000:
        raise DomainError(
            "ERR_QUOTA",
            "The local pilot operation quota is exhausted; reads/exports remain available.",
            413,
        )
    if trial and (
        trial.status != "manual_trial"
        or (trial.expires_at is not None and trial.expires_at <= now())
    ):
        raise DomainError(
            "ERR_ENTITLEMENT", "This business currently permits read/export access only.", 403
        )
    if business.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Business version changed. Reload before retrying.", 409)
    db.info["operation_id"] = uuid4()
    return business, None


def finish(
    db: Session,
    identity: Principal,
    bid: UUID,
    data: Command,
    action: str,
    result: dict[str, Any],
    journal_id: UUID | None = None,
) -> dict[str, Any]:
    from ledgerpilot.trade.models import Operation

    db.add(
        Operation(
            id=db.info["operation_id"],
            business_id=bid,
            key=data.idempotency_key,
            request_hash=fingerprint(data, action),
            action=action,
            result=result,
            journal_id=journal_id,
        )
    )
    record(db, identity.user.id, action, bid, UUID(result["id"]) if "id" in result else None)
    db.flush()
    return result


def account(
    db: Session,
    bid: UUID,
    aid: UUID,
    kinds: set[str],
    control: str | None = None,
    *,
    plain: bool = False,
) -> Account:
    item = db.scalar(select(Account).where(Account.business_id == bid, Account.id == aid))
    if (
        not item
        or not item.active
        or item.kind not in kinds
        or (control is not None and item.control != control)
        or (plain and item.control is not None)
    ):
        raise DomainError("ERR_ACCOUNT", "Account is unavailable or has an incompatible type.")
    return item


def money(value: str, business: Business, *, positive: bool = True) -> Decimal:
    amount = Decimal(value)
    if (
        not amount.is_finite()
        or amount < 0
        or (positive and amount == 0)
        or amount >= Decimal("1e18")
        or amount != round_money(amount, business.currency_precision)
    ):
        raise DomainError("ERR_AMOUNT", "Amount must use the business currency precision.")
    return amount


def line(aid: UUID, value: Decimal, debit: bool) -> LineCreate:
    return LineCreate(account_id=aid, **{"debit" if debit else "credit": format(value, "f")})


def publish(
    db: Session,
    identity: Principal,
    business: Business,
    data: Command,
    day: date,
    memo: str,
    lines: Sequence[LineCreate],
) -> UUID:
    db.execute(
        text("SELECT set_config('app.source_id',:id,true)"), {"id": str(db.info["operation_id"])}
    )
    return post(
        db,
        identity,
        business.id,
        JournalCreate(
            accounting_date=day,
            memo=memo,
            currency=business.currency,
            idempotency_key=data.idempotency_key,
            expected_version=data.expected_version,
            lines=list(lines),
        ),
    )
