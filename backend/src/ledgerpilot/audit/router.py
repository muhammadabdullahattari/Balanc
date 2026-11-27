"""FastAPI router for monthly, yearly, emergency, and auto-triggered financial audits."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import Field
from sqlalchemy import select

from ledgerpilot.audit.graph import execute_financial_audit, get_audit_graph_mermaid
from ledgerpilot.audit.models import FinancialAuditRun
from ledgerpilot.audit.nodes import _normalize_period
from ledgerpilot.audit.schemas import (
    AnyAuditMode,
    AuditGraphTopologyResponse,
    AuditRunCreateRequest,
    AuditRunDetailResponse,
    AuditRunListResponse,
)
from ledgerpilot.audit.tools import AUDIT_TOOLS
from ledgerpilot.config import Settings
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.identity.mail import deliver_workspace_email, format_mail_error_for_user
from ledgerpilot.jobs import service as job_service
from ledgerpilot.reports import visuals as report_visuals
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business

router = APIRouter(prefix="/businesses/{business_id}/audits", tags=["audits"])

_AUDIT_ALLOWED_ROLES = {"owner", "accountant", "reviewer", "auditor"}

_GRAPH_NODES = [
    "validate_request",
    "handle_invalid_request",
    "load_data",
    "empty_report",
    "retrieve_context",
    "run_rules",
    "reconcile",
    "detect_anomalies",
    "aggregate_findings",
    "flag_critical_findings",
    "polish_report",
    "fallback_report",
    "persist_and_return",
]


def _default_polished(
    summary_text: str = "Audit queued for background execution.",
) -> dict[str, Any]:
    return {
        "executive_summary": summary_text,
        "severity_breakdown": "Critical: 0 | High: 0 | Medium: 0 | Low: 0 | Total: 0",
        "findings_narrative": [],
        "next_steps": ["Refresh this audit run once background processing completes."],
        "markdown": f"# Financial Audit\n\n{summary_text}",
        "provider": "queued",
        "model_name": "pending",
        "fallback_used": False,
        "fallback_reason": "",
        "numeric_guard_passed": True,
        "chunks_processed": 0,
    }


def _serialize_row(row: FinancialAuditRun) -> dict[str, Any]:
    sev = dict(row.severity_summary or {})
    sev.setdefault("critical", 0)
    sev.setdefault("high", 0)
    sev.setdefault("medium", 0)
    sev.setdefault("low", 0)
    sev.setdefault("total", len(row.findings or []))
    sev.setdefault("health_status", "clean_verified")

    polished = dict(row.polished_report or {})
    if not polished.get("executive_summary"):
        polished = _default_polished()

    return {
        "id": row.id,
        "business_id": row.business_id,
        "audit_type": row.audit_type,
        "trigger_source": row.trigger_source,
        "status": row.status,
        "thread_id": row.thread_id,
        "job_id": row.job_id,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "scope_filters": row.scope_filters or {},
        "loaded_data_summary": row.loaded_data_summary or {},
        "severity_summary": sev,
        "findings": row.findings or [],
        "raw_report": row.raw_report or {},
        "polished_report": polished,
        "retrieved_context": row.retrieved_context or [],
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


@router.get("/graph", response_model=AuditGraphTopologyResponse)
def get_graph_diagram(
    business_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Return the compiled LangGraph Mermaid diagram, node list, and registered tool names."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    return {
        "mermaid": get_audit_graph_mermaid(),
        "nodes": _GRAPH_NODES,
        "tools": [t.name for t in AUDIT_TOOLS],
    }


@router.post("", response_model=AuditRunDetailResponse, status_code=status.HTTP_201_CREATED)
def create_audit_run(
    business_id: UUID,
    body: AuditRunCreateRequest,
    request: Request,
    response: Response,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Trigger a monthly, yearly, or emergency read-only financial audit (sync or async job)."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    settings: Settings = request.app.state.settings

    req_params: dict[str, Any] = {
        "month": body.month,
        "year": body.year,
        "calendar_year": body.calendar_year,
        "start_date": body.start_date.isoformat() if body.start_date else None,
        "end_date": body.end_date.isoformat() if body.end_date else None,
    }

    try:
        start_dt, end_dt, _label, _fm = _normalize_period(
            db, business_id, body.audit_type, req_params
        )
    except ValueError as exc:
        raise DomainError("ERR_INVALID_AUDIT_PERIOD", str(exc), 422) from None

    run_id = uuid4()
    thread_id = f"audit-{business_id}-{run_id}"

    if body.run_async:
        response.status_code = status.HTTP_202_ACCEPTED
        job = job_service.enqueue(
            db,
            business_id=business_id,
            created_by=identity.user.id,
            queue="reports",
            kind="financial_audit",
            payload={
                "audit_run_id": str(run_id),
                "audit_type": body.audit_type,
                "request_params": req_params,
            },
        )
        queued_row = FinancialAuditRun(
            id=run_id,
            business_id=business_id,
            audit_type=body.audit_type,
            trigger_source="background_job",
            period_start=start_dt,
            period_end=end_dt,
            scope_filters={},
            status="queued",
            job_id=job.id,
            thread_id=thread_id,
            loaded_data_summary={},
            severity_summary={
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "total": 0,
                "health_status": "queued",
            },
            findings=[],
            raw_report={"status": "queued", "job_id": str(job.id)},
            polished_report=_default_polished(),
            retrieved_context=[],
            metadata_json={"job_id": str(job.id), "queued_at": datetime.now(UTC).isoformat()},
            created_by=identity.user.id,
        )
        db.add(queued_row)
        db.flush()
        return _serialize_row(queued_row)

    result = execute_financial_audit(
        db,
        business_id=business_id,
        actor_id=identity.user.id,
        audit_type=body.audit_type,
        trigger_source="manual",
        request_params=req_params,
        settings=settings,
        run_id=run_id,
        persist=True,
    )
    db.flush()
    stored = db.get(FinancialAuditRun, run_id)
    if stored is not None:
        return _serialize_row(stored)

    return {
        "id": UUID(str(result["id"])),
        "business_id": business_id,
        "audit_type": body.audit_type,
        "trigger_source": "manual",
        "status": result["status"],
        "thread_id": result["thread_id"],
        "job_id": None,
        "period_start": start_dt,
        "period_end": end_dt,
        "scope_filters": result["scope_filters"],
        "loaded_data_summary": result["loaded_data_summary"],
        "severity_summary": result["severity_summary"],
        "findings": result["findings"],
        "raw_report": result["raw_report"],
        "polished_report": result["polished_report"],
        "retrieved_context": result["retrieved_context"],
        "metadata": result["metadata"],
        "created_by": identity.user.id,
        "created_at": datetime.now(UTC),
    }


@router.get("", response_model=AuditRunListResponse)
def list_audit_runs(
    business_id: UUID,
    identity: Identity,
    db: DB,
    audit_type: AnyAuditMode | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: UUID | None = None,
) -> dict[str, Any]:
    """List past manual and auto-triggered financial audit runs for this workspace."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    stmt = select(FinancialAuditRun).where(FinancialAuditRun.business_id == business_id)
    if audit_type is not None:
        stmt = stmt.where(FinancialAuditRun.audit_type == audit_type)
    if cursor is not None:
        cursor_row = db.get(FinancialAuditRun, cursor)
        if cursor_row is not None and cursor_row.business_id == business_id:
            stmt = stmt.where(
                (FinancialAuditRun.created_at < cursor_row.created_at)
                | (
                    (FinancialAuditRun.created_at == cursor_row.created_at)
                    & (FinancialAuditRun.id < cursor_row.id)
                )
            )
    stmt = stmt.order_by(FinancialAuditRun.created_at.desc(), FinancialAuditRun.id.desc()).limit(
        limit + 1
    )
    rows = list(db.scalars(stmt))
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = []
    for r in page_rows:
        sev = dict(r.severity_summary or {})
        sev.setdefault("critical", 0)
        sev.setdefault("high", 0)
        sev.setdefault("medium", 0)
        sev.setdefault("low", 0)
        sev.setdefault("total", len(r.findings or []))
        sev.setdefault("health_status", "clean_verified")
        exec_sum = str(
            (r.polished_report or {}).get("executive_summary")
            or f"{r.audit_type} audit ({r.period_start}..{r.period_end})"
        )
        items.append(
            {
                "id": r.id,
                "audit_type": r.audit_type,
                "trigger_source": r.trigger_source,
                "status": r.status,
                "job_id": r.job_id,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "severity_summary": sev,
                "executive_summary": exec_sum,
                "created_at": r.created_at,
            }
        )
    return {
        "items": items,
        "next_cursor": str(page_rows[-1].id) if has_more and page_rows else None,
    }


class AuditEmailRequest(Schema):
    recipient_email: str = Field(min_length=3, max_length=254)
    theme: Literal["light", "dark"] = "light"


@router.get("/visuals")
def get_audit_visuals(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    audit_id: UUID | None = None,
    audit_type: str = "all",
    limit_runs: Annotated[int, Query(ge=1, le=50)] = 12,
) -> dict[str, Any]:
    """Return SQL-aggregated severity, category, and historical trend visuals for audit runs."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    settings: Settings = request.app.state.settings
    return report_visuals.query_audit_visuals(
        db,
        settings,
        business_id,
        audit_id=audit_id,
        audit_type=audit_type,
        limit_runs=limit_runs,
    )


@router.get(
    "/{audit_id}/export",
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
def export_audit_report(
    business_id: UUID,
    audit_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    format: Literal["pdf", "html"] = "pdf",
    theme: Literal["light", "dark"] = "light",
) -> Response:
    """Download a PDF or HTML Financial Audit report with rendered charts + findings table."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    settings: Settings = request.app.state.settings
    built = report_visuals.build_audit_visual_export(
        db, settings, business_id, audit_id, fmt=format, theme=theme
    )
    return Response(
        content=built["bytes"],
        media_type=built["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{built["filename"]}"',
            "X-Visual-Source": str(built["visual_source"]),
        },
    )


@router.post("/{audit_id}/email")
def email_audit_report(
    business_id: UUID,
    audit_id: UUID,
    body: AuditEmailRequest,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Render an HTML audit report with embedded PNG charts and send via confirmed email."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    settings: Settings = request.app.state.settings
    biz = get_business(db, business_id)
    built = report_visuals.build_audit_visual_export(
        db, settings, business_id, audit_id, fmt="html", theme=body.theme
    )
    html_body = built["bytes"].decode("utf-8")
    plain_text = (
        f"{biz.name} — Financial Audit Report ({audit_id})\n"
        "View the HTML version of this email for embedded severity, category, and trend charts."
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
        f"{biz.name} — Financial Audit Report",
        plain_text,
        tag="audit_visual_report",
        html_body=html_body,
    )
    if not ok:
        err_hint = format_mail_error_for_user(errors)
        raise DomainError(
            "ERR_EMAIL_DELIVERY_FAILED",
            f"Could not email audit report to {body.recipient_email.strip()}. {err_hint}",
            502,
        )
    return {
        "status": "sent",
        "provider": provider,
        "recipient": body.recipient_email.strip(),
        "visual_source": built["visual_source"],
    }


@router.get("/{audit_id}", response_model=AuditRunDetailResponse)
def get_audit_run(
    business_id: UUID,
    audit_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Fetch full structured and polished details for a single financial audit run."""
    authorize(db, identity, business_id, _AUDIT_ALLOWED_ROLES)
    row = db.scalar(
        select(FinancialAuditRun).where(
            FinancialAuditRun.business_id == business_id,
            FinancialAuditRun.id == audit_id,
        )
    )
    if row is None:
        raise DomainError("ERR_NOT_FOUND", "Financial audit run not found.", 404)
    return _serialize_row(row)
