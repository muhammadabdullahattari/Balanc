"""Preview and execute disposal of expired managed export copies."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.workspace.models import ExportSnapshot
from ledgerpilot.workspace.retention import Eligibility, EligibilityInput, LegalHold, gate, preview

router = APIRouter(prefix="/businesses/{business_id}/retention")


class ExportDecision(BaseModel):
    id: UUID
    expires_at: datetime
    decision: Literal["held", "eligible", "retained"]


class ExportPreview(BaseModel):
    items: list[ExportDecision]
    truncated: bool
    execution_enabled: bool


class DisposalSelection(Schema):
    ids: list[UUID] = Field(min_length=1, max_length=100)


def execution_enabled(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(
        settings.p2_export_disposal_enabled and settings.p2_export_disposal_approval.strip()
    )


def held(db: DB, business_id: UUID) -> bool:
    return (
        db.scalar(
            select(LegalHold.id)
            .where(LegalHold.business_id == business_id, LegalHold.released_at.is_(None))
            .limit(1)
        )
        is not None
    )


@router.get("/disposal/exports", response_model=ExportPreview)
def preview_exports(
    business_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    gate(request, business_id)
    on_hold = held(db, business_id)
    rows = db.execute(
        select(ExportSnapshot.id, ExportSnapshot.expires_at)
        .where(
            ExportSnapshot.business_id == business_id, ExportSnapshot.created_by == identity.user.id
        )
        .order_by(ExportSnapshot.expires_at, ExportSnapshot.id)
        .limit(101)
    ).all()
    timestamp = db.scalar(select(func.statement_timestamp()))
    return {
        "items": [
            {
                "id": row.id,
                "expires_at": row.expires_at,
                "decision": "held"
                if on_hold
                else ("eligible" if row.expires_at <= timestamp else "retained"),
            }
            for row in rows[:100]
        ],
        "truncated": len(rows) > 100,
        "execution_enabled": execution_enabled(request),
    }


@router.post("/disposal/exports")
def dispose_exports(
    business_id: UUID, data: DisposalSelection, request: Request, identity: Identity, db: DB
) -> dict[str, list[UUID]]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    gate(request, business_id)
    if not execution_enabled(request):
        raise DomainError("ERR_DISPOSAL_DISABLED", "Export disposal has not been enabled.", 409)
    get_business(db, business_id, lock=True)
    if held(db, business_id):
        raise DomainError("ERR_LEGAL_HOLD", "An active hold blocks disposal.", 409)
    if len(set(data.ids)) != len(data.ids):
        raise DomainError("ERR_DISPOSAL_SELECTION", "Select distinct exports.", 422)
    ids = list(
        db.scalars(
            delete(ExportSnapshot)
            .where(
                ExportSnapshot.business_id == business_id,
                ExportSnapshot.created_by == identity.user.id,
                ExportSnapshot.id.in_(data.ids),
                ExportSnapshot.expires_at <= func.statement_timestamp(),
            )
            .returning(ExportSnapshot.id)
        )
    )
    if set(ids) != set(data.ids):
        raise DomainError(
            "ERR_DISPOSAL_SELECTION", "Refresh the preview and select expired exports.", 409
        )
    for identifier in ids:
        record(
            db,
            identity.user.id,
            "retention.export_disposed",
            business_id,
            identifier,
            {"reason": request.app.state.settings.p2_export_disposal_approval},
        )
    return {"deleted": ids}


class StorageAssessment(EligibilityInput):
    location: Literal["original_record", "document_storage", "backup", "external_copy"]


class StorageDecision(Eligibility):
    location: str
    decision: str
    execution_supported: bool


@router.post("/disposal/assess", response_model=StorageDecision)
def assess_storage(
    business_id: UUID, data: StorageAssessment, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    eligibility = preview(business_id, data, request, identity, db)
    return {
        **eligibility.model_dump(),
        "location": data.location,
        "decision": "held"
        if eligibility.active_hold_ids
        else ("retained" if not eligibility.retention_elapsed else "manual_review"),
        "execution_supported": False,
    }
