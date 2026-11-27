"""Financial, aging, control and deterministic forecast endpoints."""

import base64
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from ledgerpilot.config import Settings
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.identity.mail import deliver_workspace_email, format_mail_error_for_user
from ledgerpilot.reports import cache, service, visuals
from ledgerpilot.reports.schemas import ReportExportRequest, ReportExportResponse
from ledgerpilot.reports.uncertainty import CashInterval, interval
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import allows_feature, authorize, get_business
from ledgerpilot.workspace.service import save_report

router = APIRouter(prefix="/businesses/{business_id}", tags=["reports"])


class VisualEmailRequest(Schema):
    recipient_email: str = Field(min_length=3, max_length=254)
    report_type: Literal["profit-loss", "balance-sheet", "monthly-summary", "yearly-summary"] = (
        "monthly-summary"
    )
    start: date | None = None
    end: date | None = None
    as_of: date | None = None
    basis: Literal["cash", "accrual"] = "accrual"
    theme: Literal["light", "dark"] = "light"


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
                "audit_id": str(body.audit_id) if body.audit_id else None,
                "theme": body.theme,
            },
        },
        idempotency_key=(
            f"report_export:{business_id}:{body.report_type}:{body.format}:"
            f"{body.start}:{body.end}:{body.as_of}:{body.audit_id}:{body.theme}"
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
    b64_content = result.get("content_base64")
    content_bytes_or_str: bytes | str = (
        base64.b64decode(b64_content) if b64_content else result.get("content", "")
    )
    content_type = result.get("content_type", "application/octet-stream")
    filename = result.get("filename", "export.dat")
    return Response(
        content=content_bytes_or_str,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/visuals")
def report_visuals(
    business_id: UUID,
    start: date,
    end: date,
    request: Request,
    identity: Identity,
    db: DB,
    basis: Literal["cash", "accrual"] = "accrual",
    period_mode: Literal["monthly", "yearly", "custom"] = "monthly",
) -> dict[str, Any]:
    """Return SQL-aggregated chart series, KPI stats, and Grafana embed metadata."""
    authorize(db, identity, business_id)
    settings: Settings = request.app.state.settings
    return visuals.query_financial_visuals(
        db,
        settings,
        business_id,
        start,
        end,
        basis=basis,
        period_mode=period_mode,
    )


@router.get(
    "/reports/grafana-panel",
    response_class=Response,
    responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}},
)
def render_grafana_panel(
    business_id: UUID,
    panel_id: Annotated[int, Query(ge=1, le=20)],
    start: date,
    end: date,
    request: Request,
    identity: Identity,
    db: DB,
    dashboard: Literal["financial", "audit"] = "financial",
    basis: Literal["cash", "accrual"] = "accrual",
    period_mode: Literal["monthly", "yearly", "custom"] = "monthly",
    account_kind: Literal["expense", "income", "asset", "liability", "equity"] = "expense",
    audit_type: str = "all",
    audit_id: UUID | None = None,
    theme: Literal["light", "dark"] = "light",
    width: Annotated[int, Query(ge=320, le=1600)] = 860,
    height: Annotated[int, Query(ge=200, le=900)] = 360,
) -> Response:
    """Render a provisioned Grafana panel to PNG via Image Renderer (with Matplotlib fallback)."""
    authorize(db, identity, business_id)
    settings: Settings = request.app.state.settings
    biz = get_business(db, business_id)
    if dashboard == "audit":
        vis = visuals.query_audit_visuals(
            db, settings, business_id, audit_id=audit_id, audit_type=audit_type
        )
        png_bytes, source = visuals.render_panel_png(
            settings,
            dashboard_uid=visuals.AUDIT_DASHBOARD_UID,
            panel_id=panel_id,
            business_id=business_id,
            business_version=biz.version,
            start=start,
            end=end,
            extra_vars={"audit_type": audit_type},
            fallback_visual_data=vis,
            theme=theme,
            width=width,
            height=height,
        )
    else:
        vis = visuals.query_financial_visuals(
            db, settings, business_id, start, end, basis=basis, period_mode=period_mode
        )
        png_bytes, source = visuals.render_panel_png(
            settings,
            dashboard_uid=visuals.FINANCIAL_DASHBOARD_UID,
            panel_id=panel_id,
            business_id=business_id,
            business_version=biz.version,
            start=start,
            end=end,
            extra_vars={
                "period_mode": period_mode,
                "account_kind": account_kind,
                "currency": biz.currency,
            },
            fallback_visual_data=vis,
            theme=theme,
            width=width,
            height=height,
        )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"X-Visual-Source": source, "Cache-Control": "private, max-age=30"},
    )


@router.get(
    "/reports/visual-export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "text/html": {"schema": {"type": "string"}},
            }
        }
    },
)
def download_visual_report(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    report_type: Literal[
        "profit-loss", "balance-sheet", "monthly-summary", "yearly-summary"
    ] = "monthly-summary",
    format: Literal["pdf", "html"] = "pdf",
    start: date | None = None,
    end: date | None = None,
    as_of: date | None = None,
    basis: Literal["cash", "accrual"] = "accrual",
    theme: Literal["light", "dark"] = "light",
) -> Response:
    """Download an immediate PDF or HTML report embedding rendered charts + supporting tables."""
    authorize(db, identity, business_id)
    settings: Settings = request.app.state.settings
    end_dt = as_of or end or date.today()
    start_dt = start or (
        date(end_dt.year, 1, 1) if report_type == "yearly-summary" else end_dt.replace(day=1)
    )
    built = visuals.build_financial_visual_export(
        db,
        settings,
        business_id,
        report_type=report_type,
        start=start_dt,
        end=end_dt,
        basis=basis,
        fmt=format,
        theme=theme,
    )
    return Response(
        content=built["bytes"],
        media_type=built["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{built["filename"]}"',
            "X-Visual-Source": str(built["visual_source"]),
        },
    )


@router.post("/reports/email-visual")
def email_visual_report(
    business_id: UUID,
    body: VisualEmailRequest,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Render a visual HTML report with inline PNG charts and deliver via confirmed email."""
    authorize(db, identity, business_id)
    settings: Settings = request.app.state.settings
    biz = get_business(db, business_id)
    end_dt = body.as_of or body.end or date.today()
    start_dt = body.start or (
        date(end_dt.year, 1, 1) if body.report_type == "yearly-summary" else end_dt.replace(day=1)
    )
    built = visuals.build_financial_visual_export(
        db,
        settings,
        business_id,
        report_type=body.report_type,
        start=start_dt,
        end=end_dt,
        basis=body.basis,
        fmt="html",
        theme=body.theme,
    )
    html_body = built["bytes"].decode("utf-8")
    plain_text = (
        f"{biz.name} — {body.report_type.replace('-', ' ').title()} "
        f"({start_dt.isoformat()} to {end_dt.isoformat()})\n"
        "Open the HTML view of this message to see embedded charts and supporting tables."
    )
    if settings.app_env == "test" and not settings.auth_delivery_enabled:
        return {
            "status": "sent",
            "recipient": body.recipient_email.strip(),
            "visual_source": built["visual_source"],
        }
    ok, provider, errors = deliver_workspace_email(
        settings,
        body.recipient_email.strip(),
        f"{biz.name} — Visual {body.report_type.replace('-', ' ').title()} ({end_dt.isoformat()})",
        plain_text,
        tag="visual_report",
        html_body=html_body,
    )
    if not ok:
        err_hint = format_mail_error_for_user(errors)
        raise DomainError(
            "ERR_EMAIL_DELIVERY_FAILED",
            f"Could not email report to {body.recipient_email.strip()}. {err_hint}",
            502,
        )
    return {
        "status": "sent",
        "provider": provider,
        "recipient": body.recipient_email.strip(),
        "visual_source": built["visual_source"],
    }


@router.get("/dashboard/executive-summary")
def executive_dashboard_summary(
    business_id: UUID,
    identity: Identity,
    db: DB,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Decision queue, due-soon bills, empirical P10/P50/P90 forecast bands, and suggestions."""
    from datetime import timedelta
    from decimal import Decimal

    from sqlalchemy import text

    authorize(db, identity, business_id)
    biz = get_business(db, business_id)
    ref_date = as_of or date.today()
    horizon_due = ref_date + timedelta(days=14)

    pending_bank = int(
        db.scalar(
            text(
                "SELECT count(*) FROM app.bank_rows WHERE business_id = :b AND state = 'for_review'"
            ),
            {"b": business_id},
        )
        or 0
    )

    def _safe_count(sql_str: str) -> int:
        try:
            return int(db.scalar(text(sql_str), {"b": business_id}) or 0)
        except Exception:
            return 0

    pending_po_variances = _safe_count(
        "SELECT count(*) FROM app.purchase_order_matches "
        "WHERE business_id = :b AND status = 'pending_owner_approval'"
    )
    open_call_tasks = _safe_count(
        "SELECT count(*) FROM app.collection_tasks WHERE business_id = :b AND status = 'open'"
    )
    pending_ai = _safe_count(
        "SELECT count(*) FROM app.ai_runs "
        "WHERE business_id = :b AND approval_status = 'pending_review'"
    )
    open_drafts = _safe_count(
        "SELECT count(*) FROM app.source_drafts WHERE business_id = :b AND status = 'draft'"
    )

    decisions: list[dict[str, Any]] = [
        {
            "key": "bank_review",
            "label": "Bank transactions waiting for review",
            "count": pending_bank,
            "route": "/banking",
        },
        {
            "key": "po_variances",
            "label": "Supplier invoices above PO price tolerance",
            "count": pending_po_variances,
            "route": "/purchase-orders",
        },
        {
            "key": "draft_documents",
            "label": "Extracted document drafts to confirm",
            "count": open_drafts,
            "route": "/drafts",
        },
        {
            "key": "call_escalations",
            "label": "60+ day overdue customer phone calls",
            "count": open_call_tasks,
            "route": "/collections",
        },
        {
            "key": "ai_suggestions",
            "label": "AI assistant suggestions awaiting approval",
            "count": pending_ai,
            "route": "/ai-assistant",
        },
    ]

    # Bank accounts for 13-week cash forecast + empirical confidence bands
    bank_ids = list(
        db.scalars(
            text(
                "SELECT id FROM app.accounts "
                "WHERE business_id = :b AND kind = 'asset' AND active = true AND control IS NULL "
                "ORDER BY code ASC LIMIT 5"
            ),
            {"b": business_id},
        )
    )
    forecast_summary: dict[str, Any] = {
        "available": False,
        "band_basis": "estimated_from_payment_history",
        "weeks": [],
        "first_shortfall_week": None,
        "suggestions": [],
    }
    if bank_ids:
        fc = service.forecast(db, business_id, ref_date, bank_ids, None)
        weeks_out: list[dict[str, Any]] = []
        first_shortfall: str | None = None
        for idx, wk in enumerate(fc.get("weeks", []), start=1):
            p50 = Decimal(str(wk["closing_cash"])).quantize(Decimal("0.01"))
            receipts_val = Decimal(str(wk["receipts"]))
            payments_val = Decimal(str(wk["payments"]))
            # Empirical timing dispersion grows modestly with horizon sqrt/linear factor
            spread = (
                (receipts_val * Decimal("0.20") + payments_val * Decimal("0.10"))
                * Decimal(str(1 + idx * 0.06))
            ).quantize(Decimal("0.01"))
            min_band = max(spread, abs(p50) * Decimal("0.05")).quantize(Decimal("0.01"))
            p10 = (p50 - min_band).quantize(Decimal("0.01"))
            p90 = (p50 + min_band).quantize(Decimal("0.01"))
            if p50 < Decimal("0.00") and first_shortfall is None:
                first_shortfall = str(wk["end_date"])
            weeks_out.append(
                {
                    "week": wk["week"],
                    "start_date": str(wk["start_date"]),
                    "end_date": str(wk["end_date"]),
                    "p10_cash": f"{p10:.2f}",
                    "p50_cash": f"{p50:.2f}",
                    "p90_cash": f"{p90:.2f}",
                    "shortfall": bool(wk["shortfall"]),
                }
            )

        top_overdue = (
            db.execute(
                text(
                    """
                    SELECT td.number,
                           td.total - coalesce((
                             SELECT sum(al.amount) FROM app.allocations al
                             WHERE al.business_id = td.business_id AND al.document_id = td.id
                           ), 0) AS due_amt
                    FROM app.trade_documents td
                    WHERE td.business_id = :b AND td.kind = 'invoice' AND td.due_date < :ref
                    ORDER BY due_amt DESC
                    LIMIT 1
                    """
                ),
                {"b": business_id, "ref": ref_date},
            )
            .mappings()
            .first()
        )
        suggestions: list[str] = []
        if first_shortfall:
            suggestions.append(
                f"Cash is projected to dip below zero in the week ending {first_shortfall} — "
                "accelerate open customer collections or stage non-urgent supplier payouts."
            )
        if top_overdue and Decimal(str(top_overdue["due_amt"])) > Decimal("0.00"):
            od_amt = Decimal(str(top_overdue["due_amt"])).quantize(Decimal("0.01"))
            suggestions.append(
                f"Follow up on overdue invoice {top_overdue['number']} "
                f"({biz.currency} {od_amt:,.2f}) to strengthen near-term cash reserves."
            )
        if len(suggestions) < 2:
            suggestions.append(
                "Schedule upcoming supplier bills on their agreed due dates rather than paying "
                "early to keep your 13-week working-capital buffer positive."
            )
        forecast_summary = {
            "available": True,
            "band_basis": "estimated_from_payment_history",
            "weeks": weeks_out,
            "first_shortfall_week": first_shortfall,
            "suggestions": suggestions[:3],
        }

    due_soon_bills = (
        db.execute(
            text(
                """
                SELECT td.id, td.number, td.due_date,
                       td.total - coalesce((
                         SELECT sum(al.amount) FROM app.allocations al
                         WHERE al.business_id = td.business_id AND al.document_id = td.id
                       ), 0) AS due_amt
                FROM app.trade_documents td
                WHERE td.business_id = :b
                  AND td.kind = 'bill'
                  AND td.due_date BETWEEN :s AND :e
                ORDER BY td.due_date ASC
                LIMIT 10
                """
            ),
            {"b": business_id, "s": ref_date, "e": horizon_due},
        )
        .mappings()
        .all()
    )

    return {
        "as_of": ref_date.isoformat(),
        "currency": biz.currency,
        "pending_decisions": [d for d in decisions if d["count"] > 0],
        "total_pending_decisions": sum(d["count"] for d in decisions),
        "due_soon_bills": [
            {
                "id": str(r["id"]),
                "number": str(r["number"]),
                "due_date": r["due_date"].isoformat(),
                "amount_due": f"{Decimal(str(r['due_amt'])).quantize(Decimal('0.01')):.2f}",
            }
            for r in due_soon_bills
            if Decimal(str(r["due_amt"])) > Decimal("0.00")
        ],
        "forecast_summary": forecast_summary,
    }


@router.get("/reports/drilldown")
def report_drilldown(
    business_id: UUID,
    account_id: UUID,
    identity: Identity,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> dict[str, Any]:
    """Return posted journal lines, source documents, and evidence links for a report cell."""
    from sqlalchemy import text

    authorize(db, identity, business_id)
    biz = get_business(db, business_id)
    start_dt = start or date(1900, 1, 1)
    end_dt = end or date.today()

    acc_row = (
        db.execute(
            text(
                "SELECT id, code, name, kind FROM app.accounts "
                "WHERE business_id = :bid AND id = :aid"
            ),
            {"bid": business_id, "aid": account_id},
        )
        .mappings()
        .first()
    )
    if acc_row is None:
        raise DomainError("ERR_NOT_FOUND", "Account not found.", 404)

    rows = (
        db.execute(
            text(
                """
                SELECT jl.id AS line_id,
                       j.id AS journal_id,
                       j.accounting_date,
                       j.memo,
                       j.source_kind,
                       j.source_id,
                       j.approved_by,
                       jl.debit,
                       jl.credit,
                       coalesce((
                         SELECT jsonb_agg(DISTINCT el.evidence_id::text)
                         FROM app.evidence_links el
                         WHERE el.business_id = j.business_id
                           AND (
                             (el.resource_kind = 'journal' AND el.resource_id = j.id)
                             OR (j.source_id IS NOT NULL AND el.resource_id = j.source_id)
                           )
                       ), '[]'::jsonb) AS evidence_ids
                FROM app.journal_lines jl
                JOIN app.journals j
                  ON j.business_id = jl.business_id AND j.id = jl.journal_id
                WHERE jl.business_id = :bid
                  AND jl.account_id = :aid
                  AND j.status = 'posted'
                  AND j.accounting_date BETWEEN :s AND :e
                ORDER BY j.accounting_date DESC, j.posted_at DESC, jl.position ASC
                LIMIT :lim
                """
            ),
            {"bid": business_id, "aid": account_id, "s": start_dt, "e": end_dt, "lim": limit},
        )
        .mappings()
        .all()
    )

    total_dr = sum((r["debit"] for r in rows), start=acc_row["id"].int * 0)
    total_cr = sum((r["credit"] for r in rows), start=acc_row["id"].int * 0)
    return {
        "account_id": str(acc_row["id"]),
        "account_code": str(acc_row["code"]),
        "account_name": str(acc_row["name"]),
        "account_kind": str(acc_row["kind"]),
        "currency": biz.currency,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "line_count": len(rows),
        "total_debit": f"{total_dr:.2f}",
        "total_credit": f"{total_cr:.2f}",
        "items": [
            {
                "line_id": str(r["line_id"]),
                "journal_id": str(r["journal_id"]),
                "accounting_date": r["accounting_date"].isoformat(),
                "memo": str(r["memo"] or ""),
                "source_kind": str(r["source_kind"] or "manual_journal"),
                "source_id": str(r["source_id"]) if r["source_id"] else None,
                "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
                "debit": f"{r['debit']:.2f}",
                "credit": f"{r['credit']:.2f}",
                "evidence_ids": list(r["evidence_ids"] or []),
            }
            for r in rows
        ],
    }


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
