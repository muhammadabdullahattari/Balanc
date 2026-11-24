"""Two-person disposal approval, repeatable dry runs and verified payload cleanup.

Accounting metadata is never removed. Backups and external copies require an
administrator's evidence of removal; they are never presented as machine-verified.
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import delete, func, select

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.documents import storage
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.workspace.cleanup_models import (
    CleanupEvent,
    CleanupPolicy,
    CleanupRequest,
    DisposalIntent,
)
from ledgerpilot.workspace.disposal import held
from ledgerpilot.workspace.models import Evidence, EvidenceJob, ExportSnapshot
from ledgerpilot.workspace.retention import gate

router = APIRouter(prefix="/businesses/{business_id}/retention/cleanup", tags=["retention"])
RecordType = Literal["document_payload", "export_payload", "backup_copy", "external_copy"]
Location = Literal["local_evidence", "database_exports", "operator_backup", "external_storage"]
PAIRS = {
    "document_payload": "local_evidence",
    "export_payload": "database_exports",
    "backup_copy": "operator_backup",
    "external_copy": "external_storage",
}


class PolicyInput(Schema):
    record_type: RecordType
    location: Location
    retention_days: int = Field(ge=1, le=36525)
    approval_role: Literal["owner", "accountant"]
    reference: str = Field(min_length=1, max_length=500, pattern=r".*\S.*")


class PolicyView(PolicyInput):
    id: UUID
    created_by: UUID
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime


class RequestInput(Schema):
    policy_id: UUID
    resource_id: UUID
    record_date: date
    reference: str = Field(min_length=1, max_length=500, pattern=r".*\S.*")


class RequestView(RequestInput):
    id: UUID
    created_by: UUID
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime
    status: str
    attempts: int
    outcome: str


class Decision(Schema):
    eligible: bool
    reason: str
    action: str
    execution_enabled: bool


class Confirmation(Schema):
    evidence_reference: str = Field(min_length=5, max_length=500, pattern=r".*\S.*")


class EventView(Schema):
    id: UUID
    request_id: UUID
    actor_id: UUID
    action: str
    detail: dict[str, str]
    created_at: datetime


def admit(db: DB, identity: Identity, business_id: UUID, request: Request) -> None:
    authorize(db, identity, business_id, {"owner", "accountant"})
    gate(request, business_id, db)
    if request.method != "GET" and not request.url.path.endswith("/execute"):
        get_business(db, business_id, lock=True)


def policy_for(db: DB, bid: UUID, pid: UUID) -> CleanupPolicy:
    policy = db.scalar(
        select(CleanupPolicy).where(CleanupPolicy.business_id == bid, CleanupPolicy.id == pid)
    )
    if policy is None:
        raise DomainError("ERR_NOT_FOUND", "Cleanup policy not found.", 404)
    return policy


def request_for(db: DB, bid: UUID, rid: UUID) -> CleanupRequest:
    item = db.scalar(
        select(CleanupRequest).where(CleanupRequest.business_id == bid, CleanupRequest.id == rid)
    )
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Cleanup request not found.", 404)
    return item


def event(db: DB, identity: Identity, item: CleanupRequest, action: str, reason: str) -> None:
    db.add(
        CleanupEvent(
            business_id=item.business_id,
            request_id=item.id,
            actor_id=identity.user.id,
            action=action,
            detail={"reason": reason},
        )
    )
    record(
        db,
        identity.user.id,
        "retention.cleanup_" + action,
        item.business_id,
        item.id,
        {"reason": reason},
    )


def retention_elapsed(record_date: date, days: int, today: date) -> bool:
    return (today - record_date).days >= days


def decision(db: DB, item: CleanupRequest, policy: CleanupPolicy, request: Request) -> Decision:
    enabled = request.app.state.settings.p2_payload_disposal_enabled
    action = {
        "local_evidence": "Remove encrypted original file; preserve accounting metadata",
        "database_exports": "Delete expired export payload from database",
        "operator_backup": "Request operator removal; require confirmation evidence",
        "external_storage": "Request external removal; require confirmation evidence",
    }[policy.location]
    reason = "eligible"
    if held(db, item.business_id):
        reason = "active_hold"
    elif policy.approved_at is None:
        reason = "policy_not_approved"
    elif not retention_elapsed(
        item.record_date,
        policy.retention_days,
        db.execute(select(func.current_date())).scalar_one(),
    ):
        reason = "retention_not_elapsed"
    elif policy.location == "local_evidence":
        evidence = db.scalar(
            select(Evidence).where(
                Evidence.business_id == item.business_id, Evidence.id == item.resource_id
            )
        )
        active = db.scalar(
            select(EvidenceJob.id)
            .where(
                EvidenceJob.business_id == item.business_id,
                EvidenceJob.evidence_id == item.resource_id,
                EvidenceJob.state.in_(("queued", "running")),
            )
            .limit(1)
        )
        if evidence is None:
            reason = "record_not_found"
        elif active is not None:
            reason = "processing_in_progress"
    elif policy.location == "database_exports":
        if item.source_expires_at is None:
            reason = "record_not_found"
        elif item.source_expires_at > db.execute(select(func.now())).scalar_one():
            reason = "export_not_expired"
    return Decision(
        eligible=reason == "eligible", reason=reason, action=action, execution_enabled=enabled
    )


@router.get("/policies", response_model=list[PolicyView])
def list_policies(
    business_id: UUID, request: Request, identity: Identity, db: DB, offset: int = Query(0, ge=0)
) -> list[CleanupPolicy]:
    admit(db, identity, business_id, request)
    return list(
        db.scalars(
            select(CleanupPolicy)
            .where(CleanupPolicy.business_id == business_id)
            .order_by(CleanupPolicy.created_at.desc(), CleanupPolicy.id)
            .offset(offset)
            .limit(100)
        )
    )


@router.post("/policies", response_model=PolicyView, status_code=201)
def create_policy(
    business_id: UUID, data: PolicyInput, request: Request, identity: Identity, db: DB
) -> CleanupPolicy:
    admit(db, identity, business_id, request)
    if PAIRS[data.record_type] != data.location:
        raise DomainError("ERR_POLICY", "Choose the supported location for this record type.", 422)
    item = CleanupPolicy(business_id=business_id, created_by=identity.user.id, **data.model_dump())
    db.add(item)
    db.flush()
    record(db, identity.user.id, "retention.cleanup_policy_created", business_id, item.id)
    return item


@router.post("/policies/{policy_id}/approve", response_model=PolicyView)
def approve_policy(
    business_id: UUID, policy_id: UUID, request: Request, identity: Identity, db: DB
) -> CleanupPolicy:
    admit(db, identity, business_id, request)
    policy = policy_for(db, business_id, policy_id)
    authorize(db, identity, business_id, {policy.approval_role})
    if policy.created_by == identity.user.id:
        raise DomainError("ERR_INDEPENDENT_REVIEW", "A different reviewer must approve.", 409)
    if policy.approved_at is None:
        policy.approved_at = db.execute(select(func.now())).scalar_one()
        policy.approved_by = identity.user.id
        record(db, identity.user.id, "retention.cleanup_policy_approved", business_id, policy.id)
    return policy


@router.get("/requests", response_model=list[RequestView])
def list_requests(
    business_id: UUID, request: Request, identity: Identity, db: DB, offset: int = Query(0, ge=0)
) -> list[CleanupRequest]:
    admit(db, identity, business_id, request)
    return list(
        db.scalars(
            select(CleanupRequest)
            .where(CleanupRequest.business_id == business_id)
            .order_by(CleanupRequest.created_at.desc(), CleanupRequest.id)
            .offset(offset)
            .limit(100)
        )
    )


@router.post("/requests", response_model=RequestView, status_code=201)
def create_request(
    business_id: UUID, data: RequestInput, request: Request, identity: Identity, db: DB
) -> CleanupRequest:
    admit(db, identity, business_id, request)
    policy = policy_for(db, business_id, data.policy_id)
    if data.record_date > db.execute(select(func.current_date())).scalar_one():
        raise DomainError("ERR_DATE_RANGE", "Record date cannot be in the future.", 422)
    previous = db.scalar(
        select(CleanupRequest).where(
            CleanupRequest.business_id == business_id,
            CleanupRequest.policy_id == data.policy_id,
            CleanupRequest.resource_id == data.resource_id,
        )
    )
    if previous is not None:
        if previous.record_date != data.record_date or previous.reference != data.reference:
            raise DomainError("ERR_CONFLICT", "This resource already has a different request.", 409)
        return previous
    item = CleanupRequest(business_id=business_id, created_by=identity.user.id, **data.model_dump())
    if policy.location == "database_exports":
        export = db.scalar(
            select(ExportSnapshot).where(
                ExportSnapshot.business_id == business_id,
                ExportSnapshot.id == data.resource_id,
                ExportSnapshot.created_by == identity.user.id,
            )
        )
        if export is None:
            raise DomainError("ERR_NOT_FOUND", "Select an export you created.", 404)
        item.source_expires_at = export.expires_at
    elif policy.location == "local_evidence":
        source = db.scalar(
            select(Evidence.id).where(
                Evidence.business_id == business_id, Evidence.id == data.resource_id
            )
        )
        if source is None:
            raise DomainError("ERR_NOT_FOUND", "Document not found.", 404)
    db.add(item)
    db.flush()
    event(db, identity, item, "requested", data.reference)
    return item


@router.post("/requests/{request_id}/preview", response_model=Decision)
def preview_request(
    business_id: UUID, request_id: UUID, request: Request, identity: Identity, db: DB
) -> Decision:
    admit(db, identity, business_id, request)
    item = request_for(db, business_id, request_id)
    result = decision(db, item, policy_for(db, business_id, item.policy_id), request)
    event(db, identity, item, "previewed", result.reason)
    return result


@router.post("/requests/{request_id}/approve", response_model=RequestView)
def approve_request(
    business_id: UUID, request_id: UUID, request: Request, identity: Identity, db: DB
) -> CleanupRequest:
    admit(db, identity, business_id, request)
    item = request_for(db, business_id, request_id)
    policy = policy_for(db, business_id, item.policy_id)
    authorize(db, identity, business_id, {policy.approval_role})
    if item.created_by == identity.user.id:
        raise DomainError("ERR_INDEPENDENT_REVIEW", "A different reviewer must approve.", 409)
    check = decision(db, item, policy, request)
    previewed = db.scalar(
        select(CleanupEvent.id)
        .where(
            CleanupEvent.business_id == business_id,
            CleanupEvent.request_id == item.id,
            CleanupEvent.action == "previewed",
            CleanupEvent.detail["reason"].astext == "eligible",
        )
        .limit(1)
    )
    if not check.eligible or previewed is None:
        raise DomainError("ERR_DISPOSAL_REVIEW", "Run an eligible dry run before approval.", 409)
    if item.approved_at is None:
        item.approved_at = db.execute(select(func.now())).scalar_one()
        item.approved_by = identity.user.id
        item.status = "approved"
        event(db, identity, item, "approved", item.reference)
    return item


@router.post("/requests/{request_id}/execute", response_model=RequestView)
def execute_request(
    business_id: UUID, request_id: UUID, request: Request, identity: Identity, db: DB
) -> CleanupRequest:
    admit(db, identity, business_id, request)
    item = request_for(db, business_id, request_id)
    policy = policy_for(db, business_id, item.policy_id)
    if item.status in {"verified", "confirmed", "awaiting_confirmation"}:
        return item
    check = decision(db, item, policy, request)
    if not check.execution_enabled or not check.eligible or item.approved_at is None:
        raise DomainError(
            "ERR_DISPOSAL_DISABLED", "Cleanup needs enablement, approval and no holds.", 409
        )
    if policy.location == "database_exports" and item.created_by != identity.user.id:
        raise DomainError("ERR_FORBIDDEN", "Only the export creator can execute its cleanup.", 403)
    item.attempts += 1
    if policy.location in {"operator_backup", "external_storage"}:
        item.status = "awaiting_confirmation"
        item.outcome = "Operator must remove the exact copy and supply confirmation evidence."
        event(db, identity, item, item.status, item.outcome)
        db.flush()
        return item

    if policy.location == "local_evidence":
        evidence = db.scalar(
            select(Evidence).where(
                Evidence.business_id == business_id, Evidence.id == item.resource_id
            )
        )
        if evidence is None:
            item.status = "failed"
            item.outcome = "Document not found."
            event(db, identity, item, item.status, item.outcome)
            db.flush()
            return item

        expected_key = f"{business_id}/{item.resource_id}.bin"
        if evidence.object_key != expected_key:
            item.status = "failed"
            item.outcome = "Unexpected managed object key."
            event(db, identity, item, item.status, item.outcome)
            db.flush()
            return item

        # STEP A: Record durable execution intent in PostgreSQL BEFORE unlinking any file
        from uuid import uuid4

        from sqlalchemy.orm import Session as ScopedSession

        from ledgerpilot.database import set_context

        intent_id = uuid4()
        bind = getattr(db, "get_bind", None)
        if bind and callable(bind):
            with ScopedSession(bind()) as intent_db:
                set_context(intent_db, identity.user.id, business_id)
                existing = intent_db.scalar(
                    select(DisposalIntent)
                    .where(
                        DisposalIntent.business_id == business_id,
                        DisposalIntent.cleanup_request_id == item.id,
                        DisposalIntent.state.in_(["intent_recorded", "unlinked"]),
                    )
                    .order_by(DisposalIntent.created_at.desc())
                    .limit(1)
                )
                if existing is None:
                    intent_record = DisposalIntent(
                        id=intent_id,
                        business_id=business_id,
                        cleanup_request_id=item.id,
                        resource_id=item.resource_id,
                        location=policy.location,
                        object_key=expected_key,
                        state="intent_recorded",
                    )
                    intent_db.add(intent_record)
                    intent_db.commit()
                else:
                    intent_id = existing.id

        item.status = "disposing"
        item.outcome = "Disposal intent recorded; verifying holds before unlinking."
        db.flush()

        # Pre-unlink check: If a legal hold is placed while disposal is in-flight, catch it
        if held(db, business_id):
            if bind and callable(bind):
                with ScopedSession(bind()) as intent_db:
                    set_context(intent_db, identity.user.id, business_id)
                    intent_row = intent_db.scalar(
                        select(DisposalIntent).where(DisposalIntent.id == intent_id)
                    )
                    if intent_row is not None:
                        intent_row.state = "rolled_back"
                        intent_db.commit()
            item.status = "approved"
            item.outcome = "Disposal blocked by active legal hold prior to unlinking."
            event(db, identity, item, "held", item.outcome)
            db.flush()
            raise DomainError("ERR_LEGAL_HOLD", "An active hold blocks disposal.", 409)

        # STEP B: Unlink from storage
        try:
            storage.delete(request.app.state.settings, expected_key)
            if storage.exists(request.app.state.settings, expected_key):
                raise OSError("Deletion verification failed")
            if bind and callable(bind):
                with ScopedSession(bind()) as intent_db:
                    set_context(intent_db, identity.user.id, business_id)
                    intent_row = intent_db.scalar(
                        select(DisposalIntent).where(DisposalIntent.id == intent_id)
                    )
                    if intent_row is not None:
                        intent_row.state = "unlinked"
                        intent_db.commit()
        except (OSError, DomainError):
            item.status = "failed"
            item.outcome = "Storage operation failed; retry after resolving storage access."
            event(db, identity, item, item.status, item.outcome)
            db.flush()
            return item

        # STEP C: Finalize verified disposal
        if bind and callable(bind):
            with ScopedSession(bind()) as intent_db:
                set_context(intent_db, identity.user.id, business_id)
                intent_row = intent_db.scalar(
                    select(DisposalIntent).where(DisposalIntent.id == intent_id)
                )
                if intent_row is not None:
                    intent_row.state = "completed"
                    intent_db.commit()

        item.status = "verified"
        item.outcome = "Managed payload absent; accounting metadata retained."
        event(db, identity, item, item.status, item.outcome)
        db.flush()
        return item

    elif policy.location == "database_exports":
        from uuid import uuid4

        intent = DisposalIntent(
            id=uuid4(),
            business_id=business_id,
            cleanup_request_id=item.id,
            resource_id=item.resource_id,
            location=policy.location,
            object_key=f"export:{item.resource_id}",
            state="intent_recorded",
        )
        db.add(intent)
        item.status = "disposing"
        db.flush()

        if held(db, business_id):
            intent.state = "rolled_back"
            item.status = "approved"
            item.outcome = "Disposal blocked by active legal hold."
            event(db, identity, item, "held", item.outcome)
            db.flush()
            raise DomainError("ERR_LEGAL_HOLD", "An active hold blocks disposal.", 409)

        db.execute(
            delete(ExportSnapshot).where(
                ExportSnapshot.business_id == business_id,
                ExportSnapshot.id == item.resource_id,
            )
        )
        intent.state = "completed"
        item.status = "verified"
        item.outcome = "Managed payload absent; accounting metadata retained."
        event(db, identity, item, item.status, item.outcome)
        db.flush()
        return item
        event(db, identity, item, item.status, item.outcome)
        db.commit()
        return item

    return item


def reconcile_disposals(
    db: DB, settings: Settings, business_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Detect interrupted or orphaned disposal intents and safely advance or roll back state."""
    from typing import Any

    query = select(DisposalIntent).where(DisposalIntent.state.in_(["intent_recorded", "unlinked"]))
    if business_id is not None:
        query = query.where(DisposalIntent.business_id == business_id)

    pending_intents = list(db.scalars(query.order_by(DisposalIntent.created_at.asc())))
    reconciled: list[dict[str, Any]] = []

    for intent in pending_intents:
        req = db.scalar(
            select(CleanupRequest).where(
                CleanupRequest.business_id == intent.business_id,
                CleanupRequest.id == intent.cleanup_request_id,
            )
        )
        is_held = held(db, intent.business_id)

        if intent.state == "intent_recorded":
            if is_held:
                # Legal hold active: rollback intent, do NOT unlink file!
                intent.state = "rolled_back"
                if req is not None:
                    req.status = "approved"
                    req.outcome = "Disposal aborted during reconciliation due to active legal hold."
                db.commit()
                reconciled.append(
                    {
                        "intent_id": str(intent.id),
                        "action": "rolled_back",
                        "reason": "active_legal_hold",
                    }
                )
            else:
                # No hold: proceed to unlink and complete
                if intent.location == "local_evidence":
                    storage.delete(settings, intent.object_key)
                elif intent.location == "database_exports":
                    db.execute(
                        delete(ExportSnapshot).where(
                            ExportSnapshot.business_id == intent.business_id,
                            ExportSnapshot.id == intent.resource_id,
                        )
                    )
                intent.state = "completed"
                if req is not None:
                    req.status = "verified"
                    req.outcome = "Managed payload absent; accounting metadata retained."
                db.commit()
                reconciled.append(
                    {
                        "intent_id": str(intent.id),
                        "action": "completed",
                        "reason": "unlinked_and_verified",
                    }
                )

        elif intent.state == "unlinked":
            # File was already unlinked from storage before crash/interruption.
            # Even if a legal hold arrived afterwards, the record must NOT claim file is intact.
            if intent.location == "local_evidence":
                storage.delete(settings, intent.object_key)

            intent.state = "completed"
            if req is not None:
                req.status = "verified"
                if is_held:
                    req.outcome = (
                        "Payload unlinked prior to legal hold; accounting metadata retained."
                    )
                else:
                    req.outcome = "Managed payload absent; accounting metadata retained."
            db.commit()
            reconciled.append(
                {
                    "intent_id": str(intent.id),
                    "action": "completed",
                    "reason": "hold_after_unlink" if is_held else "restored_consistency",
                }
            )

    return reconciled


@router.post("/reconcile")
def trigger_reconciliation(
    business_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    admit(db, identity, business_id, request)
    reconciled = reconcile_disposals(db, request.app.state.settings, business_id)
    return {"reconciled": reconciled}


@router.post("/requests/{request_id}/confirm", response_model=RequestView)
def confirm_external(
    business_id: UUID,
    request_id: UUID,
    data: Confirmation,
    request: Request,
    identity: Identity,
    db: DB,
) -> CleanupRequest:
    admit(db, identity, business_id, request)
    authorize(db, identity, business_id, {"owner"})
    item = request_for(db, business_id, request_id)
    if held(db, business_id):
        raise DomainError("ERR_LEGAL_HOLD", "Active holds block confirmation.", 409)
    if item.status == "confirmed":
        return item
    if item.status != "awaiting_confirmation":
        raise DomainError("ERR_DISPOSAL_STATE", "No external confirmation is pending.", 409)
    item.status = "confirmed"
    item.outcome = "Administrator attested removal; not independently machine-verified."
    event(db, identity, item, "confirmed", data.evidence_reference)
    return item


@router.get("/requests/{request_id}/events", response_model=list[EventView])
def history(
    business_id: UUID,
    request_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    offset: int = Query(0, ge=0),
) -> list[CleanupEvent]:
    admit(db, identity, business_id, request)
    request_for(db, business_id, request_id)
    return list(
        db.scalars(
            select(CleanupEvent)
            .where(CleanupEvent.business_id == business_id, CleanupEvent.request_id == request_id)
            .order_by(CleanupEvent.created_at.desc(), CleanupEvent.id)
            .offset(offset)
            .limit(100)
        )
    )
