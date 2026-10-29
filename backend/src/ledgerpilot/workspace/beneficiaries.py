"""P1 records encrypted beneficiary-change proposals on hold; never applies them."""

from typing import Any
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.documents.storage import cipher
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.operations import fingerprint
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.repository import party
from ledgerpilot.workspace.models import BeneficiaryChange


class ChangeRequest(Schema):
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_party_version: int = Field(ge=0)
    account_name: str = Field(min_length=1, max_length=120)
    bank_name: str = Field(min_length=1, max_length=120)
    account_reference: str = Field(min_length=4, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)


def create(
    db: Session,
    who: Principal,
    bid: UUID,
    pid: UUID,
    data: ChangeRequest,
    settings: Settings,
) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    if not who.family.mfa:
        raise DomainError(
            "ERR_MFA_REQUIRED", "MFA is required for beneficiary-change requests.", 403
        )
    get_business(db, bid, lock=True)
    request_hash = fingerprint(data, f"beneficiary.{pid}.requested")
    old = db.scalar(
        select(BeneficiaryChange).where(
            BeneficiaryChange.business_id == bid, BeneficiaryChange.key == data.idempotency_key
        )
    )
    if old:
        if old.request_hash != request_hash:
            raise DomainError("ERR_IDEMPOTENCY_CONFLICT", "Request key was already used.", 409)
        return {"id": str(old.id), "status": old.status, "effective": False}
    target = party(db, bid, pid, "supplier")
    if target.version != data.expected_party_version:
        raise DomainError(
            "ERR_VERSION", "Party changed. Review current details before requesting a change.", 409
        )
    details = data.model_dump_json(exclude={"idempotency_key", "expected_party_version", "reason"})
    item = BeneficiaryChange(
        business_id=bid,
        party_id=pid,
        key=data.idempotency_key,
        request_hash=request_hash,
        encrypted_details=cipher(settings).encrypt(details.encode()).decode(),
        masked_reference="****" + data.account_reference[-4:],
        reason=data.reason,
        created_by=who.user.id,
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "beneficiary.change_held", bid, item.id, {"reason": data.reason})
    return {"id": str(item.id), "status": "held", "effective": False}
