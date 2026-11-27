"""Financial, aging, control and deterministic forecast endpoints."""

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from ledgerpilot.config import Settings
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.reports import cache, service
from ledgerpilot.reports.schemas import ReportExportRequest, ReportExportResponse
from ledgerpilot.reports.uncertainty import CashInterval, interval
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import allows_feature, authorize, get_business
from ledgerpilot.workspace.service import save_report

router = APIRouter(prefix="/businesses/{business_id}", tags=["reports"])


@router.get("/reports/forecast/{report_id}/interval", response_model=CashInterval)
def forecast_interval(
    business_id: UUID,
    report_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    week: Annotated[int, Query(ge=1, le=13)] = 1,
) -> CashInterval:
    authorize(db, identity, business_id)
    if not allows_feature(db, request.app.state.settings, business_id, "insights"):
        raise DomainError("ERR_NOT_FOUND", "Forecast intervals are unavailable.", 404)
    return interval(db, business_id, report_id, week)


@router.get(
    "/reports/forecast/{report_id}/evaluation",
    response_model=contracts.responses["reports.forecast_evaluation"],
)
def forecast_evaluation(
    business_id: UUID, report_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    if not allows_feature(db, request.app.state.settings, business_id, "insights"):
        raise DomainError("ERR_NOT_FOUND", "Forecast evaluation is unavailable.", 404)
    return service.evaluate_forecast(db, business_id, report_id)


@router.get(
    "/reports/profit-variance", response_model=contracts.responses["reports.profit_variance"]
)
def profit_variance(
    business_id: UUID,
    current_report_id: UUID,
    previous_report_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    if not allows_feature(db, request.app.state.settings, business_id, "insights"):
        raise DomainError("ERR_NOT_FOUND", "Profit comparisons are unavailable.", 404)
    return service.profit_variance(db, business_id, current_report_id, previous_report_id)


@router.get(
    "/reports/tax-support",
    response_model=contracts.responses["reports.tax_support"],
    response_model_exclude_unset=True,
)
def tax_support(
    business_id: UUID,
    start: date,
    end: date,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(db, identity, business_id, service.tax_support(db, business_id, start, end))


@router.get(
    "/dashboard",
    response_model=contracts.responses["reports.dashboard"],
    response_model_exclude_unset=True,
)
def dashboard(
    business_id: UUID,
    start: date,
    end: date,
    request: Request,
    identity: Identity,
    db: DB,
    bank_ids: Annotated[list[UUID], Query(max_length=20)],
    basis: Literal["cash", "accrual"] = "accrual",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    business = get_business(db, business_id)
    calculated = cache.report(
        db,
        request.app.state.report_cache,
        request.app.state.settings.report_cache_ttl_seconds,
        [
            "dashboard",
            str(business_id),
            str(identity.user.id),
            str(business.version),
            str(business.policy_version),
            str(start),
            str(end),
            basis,
            *map(str, bank_ids),
        ],
        lambda: service.dashboard(db, business_id, start, end, basis, bank_ids),
    )
    return save_report(db, identity, business_id, calculated)


@router.get(
    "/reports/profit-loss",
    response_model=contracts.responses["reports.profit_loss"],
    response_model_exclude_unset=True,
)
def profit_loss(
    business_id: UUID,
    start: date,
    end: date,
    identity: Identity,
    db: DB,
    basis: Literal["cash", "accrual"] = "accrual",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(
        db,
        identity,
        business_id,
        service.financial(db, business_id, start, end, basis, "profit-loss"),
    )


@router.get(
    "/reports/balance-sheet",
    response_model=contracts.responses["reports.balance_sheet"],
    response_model_exclude_unset=True,
)
def balance_sheet(
    business_id: UUID,
    as_of: date,
    request: Request,
    identity: Identity,
    db: DB,
    basis: Literal["cash", "accrual"] = "accrual",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    saved = save_report(
        db,
        identity,
        business_id,
        service.financial(db, business_id, date(1900, 1, 1), as_of, basis, "balance-sheet"),
    )
    from ledgerpilot.audit.triggers import trigger_auto_audit

    trigger_auto_audit(
        db,
        business_id=business_id,
        actor_id=identity.user.id,
        audit_type="auto_balance_sheet",
        trigger_source="balance_sheet_generated",
        start_date=as_of.replace(day=1),
        end_date=as_of,
        trigger_resource_id=str(saved["report_id"]),
        settings=request.app.state.settings,
    )
    return saved


@router.get(
    "/reports/aging",
    response_model=contracts.responses["reports.aging"],
    response_model_exclude_unset=True,
)
def aging(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    kind: Literal["invoice", "bill"] = "invoice",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(db, identity, business_id, service.aging(db, business_id, as_of, kind))


@router.get(
    "/reports/controls",
    response_model=contracts.responses["reports.controls"],
    response_model_exclude_unset=True,
)
def controls(business_id: UUID, as_of: date, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(db, identity, business_id, service.controls(db, business_id, as_of))


@router.get(
    "/reports/forecast",
    response_model=contracts.responses["reports.forecast"],
    response_model_exclude_unset=True,
)
def forecast(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    bank_ids: Annotated[list[UUID], Query(min_length=1, max_length=20)],
    request: Request,
    receipt_percent: Annotated[int | None, Query(ge=0, le=200)] = None,
    payment_percent: Annotated[int | None, Query(ge=0, le=200)] = None,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    scenario = None
    if receipt_percent is not None or payment_percent is not None:
        if not allows_feature(db, request.app.state.settings, business_id, "insights"):
            raise DomainError("ERR_NOT_FOUND", "Cash scenarios are unavailable.", 404)
        scenario = (
            100 if receipt_percent is None else receipt_percent,
            100 if payment_percent is None else payment_percent,
        )
    return save_report(
        db, identity, business_id, service.forecast(db, business_id, as_of, bank_ids, scenario)
    )


@router.post(
    "/reports/export",
    response_model=ReportExportResponse,
    status_code=202,
)
def export_report(
    business_id: UUID,
    body: ReportExportRequest,
    request: Request,
    identity: Identity,
    db: DB,
) -> ReportExportResponse:
    authorize(db, identity, business_id)
    settings: Settings = request.app.state.settings
    from ledgerpilot.jobs import service as job_service

    job = job_service.enqueue(
        db,
        business_id=business_id,
        created_by=identity.user.id,
        queue="reports",
        kind="report_export",
        payload={
            "report_type": body.report_type,
            "format": body.format,
            "parameters": {
                "start": str(body.start) if body.start else None,
                "end": str(body.end) if body.end else None,
                "as_of": str(body.as_of) if body.as_of else None,
                "basis": body.basis,
                "kind": body.kind,
                "bank_ids": [str(x) for x in body.bank_ids],
            },
        },
        idempotency_key=(
            f"report_export:{business_id}:{body.report_type}:{body.format}:"
            f"{body.start}:{body.end}:{body.as_of}"
        ),
        max_attempts=settings.jobs_default_max_attempts,
        timeout_seconds=settings.jobs_default_timeout_seconds,
    )
    return ReportExportResponse(
        job_id=job.id,
        status="queued",
        status_url=f"/api/v1/businesses/{business_id}/jobs/{job.id}",
        download_url=f"/api/v1/businesses/{business_id}/reports/export/{job.id}/download",
    )


@router.get(
    "/reports/export/{job_id}/download",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        },
        202: {"content": {"application/json": {"schema": {"type": "object"}}}},
    },
)
def download_report_export(
    business_id: UUID,
    job_id: UUID,
    identity: Identity,
    db: DB,
) -> Response:
    authorize(db, identity, business_id)
    from ledgerpilot.jobs import service as job_service

    job = job_service.get_job(db, business_id, job_id)
    if job is None:
        raise DomainError("ERR_NOT_FOUND", "Export job not found.", 404)

    if job.state in ("queued", "running"):
        return JSONResponse(
            status_code=202,
            content={
                "job_id": str(job.id),
                "state": job.state,
                "message": "Export job is still processing.",
            },
        )
    if job.state in ("failed", "dead_letter"):
        err = job.error_detail or {}
        raise DomainError(
            "ERR_EXPORT_FAILED",
            f"Export failed: {err.get('error', 'unknown error')}",
            500,
        )

    result = job.result or {}
    content = result.get("content", "")
    content_type = result.get("content_type", "application/octet-stream")
    filename = result.get("filename", "export.dat")
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/balances")
def materialized_balances(
    business_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, str]:
    """Return latest materialized balance summaries per account for the workspace."""
    authorize(db, identity, business_id)
    balances_map = service.get_materialized_balances(db, business_id)
    return {str(aid): str(amount) for aid, amount in balances_map.items()}
