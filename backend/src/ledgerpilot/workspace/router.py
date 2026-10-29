"""Internal operational APIs, scoped to the authenticated business membership."""

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import func, select, text

from ledgerpilot.accounting.models import Period
from ledgerpilot.audit.models import AuditEvent
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import Operation, TradeDocument
from ledgerpilot.workspace import beneficiaries, opening_adjustments, resumable, service
from ledgerpilot.workspace import imports as migrations
from ledgerpilot.workspace import privacy as privacy_workflows
from ledgerpilot.workspace.models import (
    BeneficiaryChange,
    CloseTask,
    Consent,
    Evidence,
    EvidenceJob,
    ImportBatch,
    Notification,
    OpeningItem,
    Preference,
    PrivacyRequest,
    ReportRun,
    TrialEntitlement,
)
from ledgerpilot.workspace.schemas import (
    AssistantRequest,
    ConsentCreate,
    MembershipUpdate,
    PreferencesUpdate,
    PrivacyCreate,
    ReasonRequest,
    ReminderRequest,
    TaskCreate,
    TaskUpdate,
)

router = APIRouter(prefix="/businesses/{business_id}", tags=["workspace"])


@router.post(
    "/opening-balances/{opening_id}/corrections",
    status_code=201,
    response_model=contracts.responses["workspace.opening_correction"],
    response_model_exclude_unset=True,
)
def opening_correction(
    business_id: UUID,
    opening_id: UUID,
    data: opening_adjustments.Correction,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return opening_adjustments.correct(db, identity, business_id, opening_id, data)


@router.post(
    "/opening-balances/{opening_id}/cash-policy",
    status_code=201,
    response_model=contracts.responses["workspace.opening_cash_policy"],
    response_model_exclude_unset=True,
)
def opening_cash_policy(
    business_id: UUID,
    opening_id: UUID,
    data: opening_adjustments.CashTreatment,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return opening_adjustments.cash_policy(db, identity, business_id, opening_id, data)


@router.get(
    "/opening-balances/{opening_id}/history",
    response_model=contracts.responses["workspace.opening_history"],
    response_model_exclude_unset=True,
)
def opening_history(
    business_id: UUID, opening_id: UUID, identity: Identity, db: DB
) -> dict[str, Any]:
    from ledgerpilot.workspace.models import OpeningCashPolicy, OpeningCorrection

    authorize(db, identity, business_id)
    opening_adjustments.source(db, business_id, opening_id)
    rows = list(
        db.scalars(
            select(OpeningCorrection)
            .where(
                OpeningCorrection.business_id == business_id,
                OpeningCorrection.opening_id == opening_id,
            )
            .order_by(OpeningCorrection.accounting_date, OpeningCorrection.id)
            .limit(1001)
        )
    )
    if len(rows) > 1000:
        raise DomainError("ERR_QUOTA", "Request a narrower operator audit export.", 413)
    policy = db.scalar(
        select(OpeningCashPolicy).where(
            OpeningCashPolicy.business_id == business_id, OpeningCashPolicy.opening_id == opening_id
        )
    )
    return {
        "corrections": [
            {
                **{
                    k: getattr(r, k)
                    for k in (
                        "id",
                        "accounting_date",
                        "document_id",
                        "loan_movement_id",
                        "journal_id",
                        "reason",
                        "reviewed_by",
                    )
                },
                "delta": str(r.delta),
            }
            for r in rows
        ],
        "cash_policy": {
            k: getattr(policy, k)
            for k in (
                "id",
                "treatment",
                "tax_treatment",
                "components",
                "reason",
                "evidence_id",
                "reviewed_by",
            )
        }
        if policy
        else None,
    }


@router.post(
    "/exports/snapshots",
    status_code=201,
    response_model=contracts.responses["workspace.export_snapshot"],
    response_model_exclude_unset=True,
)
def export_snapshot(
    business_id: UUID, data: resumable.ExportCreate, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    return resumable.create(db, identity, business_id, data, request.app.state.settings)


@router.get(
    "/exports/snapshots/{snapshot_id}",
    response_model=contracts.responses["workspace.snapshot_status"],
    response_model_exclude_unset=True,
)
def snapshot_status(
    business_id: UUID, snapshot_id: UUID, identity: Identity, db: DB
) -> dict[str, Any]:
    return resumable.metadata(resumable.owned(db, identity, business_id, snapshot_id))


@router.get(
    "/exports/snapshots/{snapshot_id}/download",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        },
        206: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        },
    },
)
def snapshot_download(
    business_id: UUID,
    snapshot_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    range_header: Annotated[str | None, Header(alias="Range", max_length=80)] = None,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=80)] = None,
) -> Response:
    item = resumable.owned(db, identity, business_id, snapshot_id)
    body, status, headers = resumable.download(
        item,
        request.app.state.settings,
        range_header,
        if_match,
    )
    record(db, identity.user.id, "export.snapshot_downloaded", business_id, snapshot_id)
    return Response(
        body,
        status_code=status,
        headers=headers,
        media_type="text/csv" if item.format == "journals_csv" else "application/zip",
    )


@router.get(
    "/privacy-review-queue",
    response_model=contracts.responses["workspace.privacy_queue"],
    response_model_exclude_unset=True,
)
def privacy_queue(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    query = select(PrivacyRequest).where(PrivacyRequest.business_id == business_id)
    if cursor:
        query = query.where(PrivacyRequest.id > cursor)
    rows = list(db.scalars(query.order_by(PrivacyRequest.id).limit(limit + 1)))
    return {
        "items": [
            {k: getattr(r, k) for k in ("id", "user_id", "kind", "reason")} for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post(
    "/privacy-requests/{privacy_id}/reviews",
    status_code=201,
    response_model=contracts.responses["workspace.review_privacy"],
    response_model_exclude_unset=True,
)
def review_privacy(
    business_id: UUID,
    privacy_id: UUID,
    data: privacy_workflows.ReviewRequest,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return privacy_workflows.review(db, identity, business_id, privacy_id, data)


@router.get(
    "/privacy-requests/{privacy_id}",
    response_model=contracts.responses["workspace.privacy_history"],
    response_model_exclude_unset=True,
)
def privacy_history(
    business_id: UUID, privacy_id: UUID, identity: Identity, db: DB
) -> dict[str, Any]:
    return privacy_workflows.history(db, identity, business_id, privacy_id)


@router.get(
    "/opening-balances",
    response_model=contracts.responses["workspace.opening_balances"],
    response_model_exclude_unset=True,
)
def opening_balances(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(OpeningItem).where(OpeningItem.business_id == business_id)
    if cursor:
        query = query.where(OpeningItem.id > cursor)
    rows = list(db.scalars(query.order_by(OpeningItem.id).limit(limit + 1)))
    return {
        "items": [
            {
                **{
                    k: getattr(r, k)
                    for k in (
                        "id",
                        "kind",
                        "party_id",
                        "reference",
                        "original_date",
                        "due_date",
                        "cutover_date",
                        "equity_account_id",
                        "document_id",
                        "loan_movement_id",
                        "reviewed_by",
                        "review_reason",
                    )
                },
                "amount": str(r.amount),
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/privacy-export",
    response_model=contracts.responses["workspace.privacy_export"],
    response_model_exclude_unset=True,
)
def privacy_export(
    business_id: UUID, identity: Identity, db: DB, response: Response
) -> dict[str, Any]:
    member = authorize(db, identity, business_id)
    if not identity.family.mfa:
        raise DomainError("ERR_MFA_REQUIRED", "Identity verification is required.", 403)
    consents = list(
        db.scalars(
            select(Consent)
            .where(Consent.business_id == business_id, Consent.user_id == identity.user.id)
            .order_by(Consent.id)
            .limit(1001)
        )
    )
    requests = list(
        db.scalars(
            select(PrivacyRequest)
            .where(
                PrivacyRequest.business_id == business_id,
                PrivacyRequest.user_id == identity.user.id,
            )
            .order_by(PrivacyRequest.id)
            .limit(1001)
        )
    )
    if len(consents) > 1000 or len(requests) > 1000:
        raise DomainError("ERR_QUOTA", "Contact the privacy reviewer for a larger export.", 413)
    current_statuses = privacy_workflows.statuses(db, business_id, [r.id for r in requests])
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = 'attachment; filename="personal-data.json"'
    record(db, identity.user.id, "privacy.self_exported", business_id)
    return {
        "scope": "own_account_and_selected_business_privacy_records",
        "user": {
            "id": identity.user.id,
            "email": identity.user.email,
            "created_at": identity.user.created_at,
        },
        "membership": {"business_id": business_id, "role": member.role, "active": member.active},
        "consents": [
            {k: getattr(r, k) for k in ("id", "purpose", "policy_version", "granted", "created_at")}
            for r in consents
        ],
        "privacy_requests": [
            {
                k: current_statuses.get(r.id, r.status) if k == "status" else getattr(r, k)
                for k in ("id", "kind", "status", "reason")
            }
            for r in requests
        ],
        "financial_records": "Use the authorized journal and evidence-pack exports.",
        "deletion_status": "No deletion performed; retention review is separate.",
    }


@router.post(
    "/jobs/{job_id}/replay",
    response_model=contracts.responses["workspace.job_replay"],
    response_model_exclude_unset=True,
)
def job_replay(
    business_id: UUID,
    job_id: UUID,
    data: ReasonRequest,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    replayed = db.scalar(
        text("SELECT app.replay_evidence_job(:b,:id,:reason)"),
        {"b": business_id, "id": job_id, "reason": data.reason},
    )
    if not replayed:
        raise DomainError(
            "ERR_JOB_STATE",
            "Only a failed quarantined job with an active creator may be replayed.",
            409,
        )
    return {"id": str(job_id), "state": "queued"}


@router.post(
    "/parties/{party_id}/beneficiary-changes",
    status_code=201,
    response_model=contracts.responses["workspace.beneficiary_request"],
    response_model_exclude_unset=True,
)
def beneficiary_request(
    business_id: UUID,
    party_id: UUID,
    data: beneficiaries.ChangeRequest,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return beneficiaries.create(
        db, identity, business_id, party_id, data, request.app.state.settings
    )


@router.get(
    "/beneficiary-changes",
    response_model=contracts.responses["workspace.beneficiary_changes"],
    response_model_exclude_unset=True,
)
def beneficiary_changes(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    query = select(BeneficiaryChange).where(BeneficiaryChange.business_id == business_id)
    if cursor:
        query = query.where(BeneficiaryChange.id > cursor)
    rows = list(db.scalars(query.order_by(BeneficiaryChange.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "party_id",
                    "status",
                    "masked_reference",
                    "reason",
                    "created_by",
                    "created_at",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/privacy-requests",
    response_model=contracts.responses["workspace.privacy_requests"],
    response_model_exclude_unset=True,
)
def privacy_requests(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(PrivacyRequest).where(
        PrivacyRequest.business_id == business_id, PrivacyRequest.user_id == identity.user.id
    )
    if cursor:
        query = query.where(PrivacyRequest.id > cursor)
    rows = list(db.scalars(query.order_by(PrivacyRequest.id).limit(limit + 1)))
    current_statuses = privacy_workflows.statuses(db, business_id, [r.id for r in rows[:limit]])
    return {
        "items": [
            {
                k: current_statuses.get(r.id, r.status) if k == "status" else getattr(r, k)
                for k in ("id", "kind", "status", "reason")
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/consents",
    response_model=contracts.responses["workspace.consents"],
    response_model_exclude_unset=True,
)
def consents(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(Consent).where(
        Consent.business_id == business_id, Consent.user_id == identity.user.id
    )
    if cursor:
        query = query.where(Consent.id > cursor)
    rows = list(db.scalars(query.order_by(Consent.id).limit(limit + 1)))
    return {
        "items": [
            {k: getattr(r, k) for k in ("id", "purpose", "policy_version", "granted", "created_at")}
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/usage",
    response_model=contracts.responses["workspace.usage"],
    response_model_exclude_unset=True,
)
def usage(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    documents, size = db.execute(
        select(func.count(Evidence.id), func.coalesce(func.sum(Evidence.size), 0)).where(
            Evidence.business_id == business_id
        )
    ).one()
    operations = (
        db.scalar(
            select(func.count()).select_from(Operation).where(Operation.business_id == business_id)
        )
        or 0
    )
    jobs = dict(
        db.execute(
            select(EvidenceJob.state, func.count())
            .where(EvidenceJob.business_id == business_id)
            .group_by(EvidenceJob.state)
        ).all()
    )
    return {
        "profile": "local_manual_trial",
        "documents": documents,
        "document_limit": 200,
        "evidence_bytes": size,
        "evidence_byte_limit": 104857600,
        "operations": operations,
        "operation_limit": 10000,
        "jobs": jobs,
        "user_per_business_per_minute": 120,
        "business_per_minute": 600,
        "automatic_paid_upgrade": False,
    }


@router.post(
    "/imports/preview",
    response_model=contracts.responses["workspace.import_preview"],
    response_model_exclude_unset=True,
)
def import_preview(
    business_id: UUID,
    data: migrations.ImportPreview,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return migrations.preview(db, identity, business_id, data)


@router.post(
    "/imports/apply",
    status_code=201,
    response_model=contracts.responses["workspace.import_apply"],
    response_model_exclude_unset=True,
)
def import_apply(
    business_id: UUID,
    data: migrations.ImportApply,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return migrations.apply(db, identity, business_id, data)


@router.get(
    "/imports",
    response_model=contracts.responses["workspace.imports_list"],
    response_model_exclude_unset=True,
)
def imports_list(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(ImportBatch).where(ImportBatch.business_id == business_id)
    if cursor:
        query = query.where(ImportBatch.id > cursor)
    rows = list(db.scalars(query.order_by(ImportBatch.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "file_hash",
                    "mode",
                    "cutover_date",
                    "results",
                    "expected_trial",
                    "created_by",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/imports/{batch_id}/comparison",
    response_model=contracts.responses["workspace.import_comparison"],
    response_model_exclude_unset=True,
)
def import_comparison(
    business_id: UUID,
    batch_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    from decimal import Decimal

    from ledgerpilot.accounting.models import Account
    from ledgerpilot.reports.service import balances, controls

    authorize(db, identity, business_id)
    item = db.scalar(
        select(ImportBatch).where(
            ImportBatch.business_id == business_id, ImportBatch.id == batch_id
        )
    )
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Import batch not found.", 404)
    if item.mode == "chart":
        return {
            "batch_id": str(item.id),
            "comparison_available": False,
            "reason": "Chart-only imports have no financial baseline.",
        }
    actual = balances(db, business_id, item.cutover_date)
    rows = []
    for account in db.scalars(
        select(Account).where(Account.business_id == business_id).order_by(Account.code)
    ):
        expected, current = (
            Decimal(item.expected_trial.get(account.code, "0")),
            actual.get(account.id, Decimal(0)),
        )
        rows.append(
            {
                "account_id": str(account.id),
                "code": account.code,
                "expected": str(expected),
                "actual": str(current),
                "difference": str(current - expected),
            }
        )
    return {
        "batch_id": str(item.id),
        "as_of": item.cutover_date,
        "items": rows,
        "balanced": all(Decimal(r["difference"]) == 0 for r in rows),
        "controls": controls(db, business_id, item.cutover_date),
        "basis": "accrual_general_ledger_baseline",
    }


@router.get(
    "/exports/verification-key",
    response_model=contracts.responses["workspace.export_key"],
    response_model_exclude_unset=True,
)
def export_key(business_id: UUID, request: Request, identity: Identity, db: DB) -> dict[str, str]:
    from ledgerpilot.workspace.exports import public_key

    authorize(db, identity, business_id)
    return public_key(request.app.state.settings)


@router.get(
    "/exports/evidence-pack.zip",
    response_class=Response,
    responses={
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def evidence_pack(
    business_id: UUID,
    start: date,
    end: date,
    request: Request,
    identity: Identity,
    db: DB,
) -> Response:
    from ledgerpilot.workspace.exports import pack

    authorize(db, identity, business_id)
    body = pack(db, request.app.state.settings, business_id, start, end, identity)
    record(db, identity.user.id, "export.evidence_pack", business_id)
    return Response(
        body,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="ledgerpilot-evidence.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/jobs", response_model=contracts.responses["workspace.jobs"], response_model_exclude_unset=True
)
def jobs(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    from ledgerpilot.workspace.models import EvidenceJob

    authorize(db, identity, business_id, {"owner", "accountant", "reviewer"})
    query = select(EvidenceJob).where(EvidenceJob.business_id == business_id)
    if cursor:
        query = query.where(EvidenceJob.id > cursor)
    rows = list(db.scalars(query.order_by(EvidenceJob.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "evidence_id",
                    "created_by",
                    "state",
                    "attempts",
                    "available_at",
                    "error_code",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/memberships",
    response_model=contracts.responses["workspace.members"],
    response_model_exclude_unset=True,
)
def members(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    rows = db.execute(text("SELECT * FROM app.list_members(:bid) LIMIT 201"), {"bid": business_id})
    result: list[dict[str, Any]] = [dict(r) for r in rows.mappings()]
    if len(result) > 200:
        raise DomainError("ERR_QUOTA", "The pilot supports at most 200 active memberships.", 413)
    return {"items": result}


@router.patch(
    "/memberships/{user_id}",
    response_model=contracts.responses["workspace.update_member"],
    response_model_exclude_unset=True,
)
def update_member(
    business_id: UUID,
    user_id: UUID,
    data: MembershipUpdate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner"})
    result: int = db.execute(
        text("SELECT app.update_member(:b,:u,:r,:a,:p,:i,:v)"),
        {
            "b": business_id,
            "u": user_id,
            "r": data.role,
            "a": data.active,
            "p": data.can_post,
            "i": data.can_invite,
            "v": data.expected_version,
        },
    ).scalar_one()
    return {"user_id": str(user_id), "version": result}


@router.get(
    "/reports/history",
    response_model=contracts.page(
        "ReportHistoryList",
        contracts.obj(
            "ReportHistoryEntry", id=UUID, kind=str, created_by=UUID, created_at=datetime
        ),
    ),
)
def report_history(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(ReportRun).where(ReportRun.business_id == business_id)
    if cursor:
        query = query.where(ReportRun.id > cursor)
    rows = list(db.scalars(query.order_by(ReportRun.id).limit(limit + 1)))
    return {
        "items": [
            {k: getattr(r, k) for k in ("id", "kind", "created_by", "created_at")}
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/reports/history/{report_id}",
    response_model=contracts.responses["workspace.report"],
    response_model_exclude_unset=True,
)
def report(business_id: UUID, report_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    item = db.scalar(
        select(ReportRun).where(ReportRun.business_id == business_id, ReportRun.id == report_id)
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Report not found.", 404)
    return item.result


@router.post(
    "/assistant/report-answer",
    response_model=contracts.responses["workspace.answer"],
    response_model_exclude_unset=True,
)
def answer(business_id: UUID, data: AssistantRequest, identity: Identity, db: DB) -> dict[str, Any]:
    result = report(business_id, data.report_id, identity, db)
    if data.metric not in result:
        raise DomainError("ERR_METRIC", "The selected report does not contain this metric.")
    return {
        "report_id": str(data.report_id),
        "metric": data.metric,
        "value": result[data.metric],
        "currency": result.get("currency"),
        "basis": result.get("basis"),
        "mode": "authorized_report_lookup",
        "model_version": None,
        "actions_executed": False,
    }


@router.get(
    "/notifications",
    response_model=contracts.responses["workspace.notifications"],
    response_model_exclude_unset=True,
)
def notifications(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(Notification).where(
        Notification.business_id == business_id, Notification.recipient_id == identity.user.id
    )
    if cursor:
        query = query.where(Notification.id > cursor)
    rows = list(db.scalars(query.order_by(Notification.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "kind",
                    "resource_id",
                    "read",
                    "created_at",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post(
    "/notifications/{notification_id}/read",
    response_model=contracts.responses["workspace.notification_read"],
    response_model_exclude_unset=True,
)
def notification_read(
    business_id: UUID,
    notification_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, bool]:
    authorize(db, identity, business_id)
    item = db.scalar(
        select(Notification).where(
            Notification.business_id == business_id,
            Notification.id == notification_id,
            Notification.recipient_id == identity.user.id,
        )
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Notification not found.", 404)
    item.read = True
    return {"read": True}


@router.get(
    "/notification-preferences",
    response_model=contracts.responses["workspace.preferences"],
    response_model_exclude_unset=True,
)
def preferences(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    item = db.scalar(
        select(Preference).where(
            Preference.business_id == business_id, Preference.user_id == identity.user.id
        )
    )
    return (
        {
            k: getattr(item, k)
            for k in (
                "marketing",
                "reminders",
                "quiet_start",
                "quiet_end",
                "version",
            )
        }
        if item
        else {
            "marketing": False,
            "reminders": True,
            "quiet_start": 21,
            "quiet_end": 8,
            "version": 0,
        }
    )


@router.patch(
    "/notification-preferences",
    response_model=contracts.responses["workspace.preference_update"],
    response_model_exclude_unset=True,
)
def preference_update(
    business_id: UUID,
    data: PreferencesUpdate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    get_business(db, business_id, lock=True)
    item = db.scalar(
        select(Preference).where(
            Preference.business_id == business_id, Preference.user_id == identity.user.id
        )
    )
    if not item:
        if data.expected_version != 0:
            raise DomainError("ERR_VERSION", "Preferences changed.", 409)
        item = Preference(business_id=business_id, user_id=identity.user.id, version=0)
        db.add(item)
    if item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Preferences changed.", 409)
    for key, value in data.model_dump(exclude={"expected_version"}).items():
        setattr(item, key, value)
    item.version += 1
    record(db, identity.user.id, "preferences.updated", business_id)
    db.flush()
    return preferences(business_id, identity, db)


@router.post(
    "/reminders/review",
    response_model=contracts.responses["workspace.review_reminders"],
    response_model_exclude_unset=True,
)
def review_reminders(
    business_id: UUID,
    data: ReminderRequest,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    return {
        "created": service.reminders(db, identity, business_id, data.as_of),
        "channel": "in_app",
        "external_messages_sent": 0,
    }


@router.post(
    "/close-tasks",
    status_code=201,
    response_model=contracts.responses["workspace.create_task"],
    response_model_exclude_unset=True,
)
def create_task(business_id: UUID, data: TaskCreate, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    period = db.scalar(
        select(Period).where(Period.business_id == business_id, Period.id == data.period_id)
    )
    assigned = db.scalar(
        text("SELECT app.member_active(:b,:u)"), {"b": business_id, "u": data.assigned_to}
    )
    if not period or period.locked or not assigned:
        raise DomainError(
            "ERR_CLOSE_TASK", "An open period and active assigned member are required."
        )
    item = CloseTask(business_id=business_id, **data.model_dump())
    db.add(item)
    db.flush()
    record(db, identity.user.id, "close.task_created", business_id, item.id)
    return {"id": str(item.id), "version": item.version, "state": item.state}


@router.get(
    "/close-tasks",
    response_model=contracts.responses["workspace.tasks"],
    response_model_exclude_unset=True,
)
def tasks(
    business_id: UUID,
    period_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(CloseTask).where(
        CloseTask.business_id == business_id, CloseTask.period_id == period_id
    )
    if cursor:
        query = query.where(CloseTask.id > cursor)
    rows = list(db.scalars(query.order_by(CloseTask.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "period_id",
                    "name",
                    "assigned_to",
                    "required",
                    "state",
                    "prepared_by",
                    "reviewed_by",
                    "evidence_id",
                    "comment",
                    "version",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.patch(
    "/close-tasks/{task_id}",
    response_model=contracts.responses["workspace.task_update"],
    response_model_exclude_unset=True,
)
def task_update(
    business_id: UUID,
    task_id: UUID,
    data: TaskUpdate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
    item = service.update_task(db, identity, business_id, task_id, data)
    return {"id": str(item.id), "state": item.state, "version": item.version}


@router.get(
    "/readiness",
    response_model=contracts.responses["workspace.readiness"],
    response_model_exclude_unset=True,
)
def readiness(business_id: UUID, as_of: date, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    from ledgerpilot.reports.service import context

    return service.save_report(
        db,
        identity,
        business_id,
        {
            **context(db, business_id, as_of, "operational_readiness"),
            "kind": "readiness",
            **service.readiness(db, business_id, as_of),
        },
    )


@router.get(
    "/audit-events",
    response_model=contracts.responses["workspace.audits"],
    response_model_exclude_unset=True,
)
def audits(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "reviewer", "auditor"})
    query = select(AuditEvent).where(AuditEvent.business_id == business_id)
    if cursor:
        query = query.where(AuditEvent.id > cursor)
    rows = list(db.scalars(query.order_by(AuditEvent.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "user_id",
                    "action",
                    "resource_id",
                    "occurred_at",
                    "details",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/search",
    response_model=contracts.responses["workspace.search"],
    response_model_exclude_unset=True,
)
def search(
    business_id: UUID,
    identity: Identity,
    db: DB,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(25, ge=1, le=100),
    cursor: UUID | None = None,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    term = func.plainto_tsquery("simple", q)
    query = select(TradeDocument).where(
        TradeDocument.business_id == business_id,
        func.to_tsvector("simple", TradeDocument.number).op("@@")(term),
    )
    if cursor:
        query = query.where(TradeDocument.id > cursor)
    rows = list(db.scalars(query.order_by(TradeDocument.id).limit(limit + 1)))
    return {
        "items": [
            {"id": str(r.id), "kind": r.kind, "number": r.number, "party_id": str(r.party_id)}
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/exports/journals.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(
    business_id: UUID,
    start: date,
    end: date,
    identity: Identity,
    db: DB,
) -> Response:
    authorize(db, identity, business_id)
    result = service.journal_csv(db, business_id, start, end)
    record(db, identity.user.id, "export.journals", business_id)
    return Response(
        result,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="journals.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/consents",
    status_code=201,
    response_model=contracts.responses["workspace.consent"],
    response_model_exclude_unset=True,
)
def consent(business_id: UUID, data: ConsentCreate, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    item = Consent(business_id=business_id, user_id=identity.user.id, **data.model_dump())
    db.add(item)
    db.flush()
    record(db, identity.user.id, "consent.recorded", business_id, item.id)
    return {"id": str(item.id), "purpose": item.purpose, "granted": item.granted}


@router.post(
    "/privacy-requests",
    status_code=201,
    response_model=contracts.responses["workspace.privacy"],
    response_model_exclude_unset=True,
)
def privacy(business_id: UUID, data: PrivacyCreate, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    if not identity.family.mfa:
        raise DomainError("ERR_MFA_REQUIRED", "Identity verification is required.", 403)
    item = PrivacyRequest(business_id=business_id, user_id=identity.user.id, **data.model_dump())
    db.add(item)
    db.flush()
    record(db, identity.user.id, "privacy.requested", business_id, item.id)
    return {
        "id": str(item.id),
        "status": item.status,
        "retained_categories": ["posted financial history", "audit and consent records"],
        "retention_basis": "requires jurisdiction and legal-hold review",
        "deleted": False,
    }


@router.get(
    "/entitlement",
    response_model=contracts.responses["workspace.entitlement"],
    response_model_exclude_unset=True,
)
def entitlement(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    from ledgerpilot.identity.security import now

    authorize(db, identity, business_id)
    item = db.scalar(select(TrialEntitlement).where(TrialEntitlement.business_id == business_id))
    return {
        "status": item.status if item else "manual_trial",
        "live_charges": False,
        "expires_at": item.expires_at if item else None,
        "write_allowed": bool(
            item
            and item.status == "manual_trial"
            and (item.expires_at is None or item.expires_at > now())
        ),
        "bank_import_row_limit": item.import_row_limit if item else 500,
        "document_byte_limit": item.document_byte_limit if item else 26214400,
    }
