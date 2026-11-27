"""Background job worker handlers for webhooks, email outbox, quickbooks import,
and report exports.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.config import get_settings
from ledgerpilot.identity.mail import deliver
from ledgerpilot.provider_integrations import execute_quickbooks_import, load, process_stripe_event
from ledgerpilot.reports.service import generate_report_export
from ledgerpilot.shared.errors import DomainError

logger = logging.getLogger("ledgerpilot.jobs.handlers")


def handle_stripe_webhook(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event_id = payload["event_id"]
    event = payload["event"]
    settings = get_settings()
    try:
        process_stripe_event(db, settings, event)
        db.execute(
            text("SELECT app.stripe_record_event_result(:id, 'processed')"),
            {"id": event_id},
        )
        return {"event_id": event_id, "status": "processed"}
    except Exception as exc:
        db.execute(
            text("SELECT app.stripe_record_event_result(:id, 'failed', :err)"),
            {"id": event_id, "err": str(exc)},
        )
        raise


def handle_identity_smtp(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    recipient = payload["recipient"]
    purpose = payload["purpose"]
    token = payload["token"]
    settings = get_settings()
    deliver(settings, recipient, purpose, token)
    return {"recipient": recipient, "purpose": purpose, "delivered": True}


def handle_quickbooks_import(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if business_id is None:
        raise ValueError("QuickBooks import requires business_id")
    permitted = db.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM app.memberships m JOIN app.users u ON u.id=m.user_id "
            "WHERE m.business_id=:business AND m.user_id=:actor AND m.active AND NOT u.suspended "
            "AND (m.role='accountant' OR (m.role='owner' AND m.can_post)))"
        ),
        {"business": business_id, "actor": created_by},
    )
    if not created_by or not permitted:
        raise DomainError("ERR_IMPORT_ACTOR", "An authorized member must start a new import.", 403)
    settings = get_settings()
    current = load(db, business_id, "quickbooks")
    cutover_str = payload.get("cutover_date")
    cutover = date.fromisoformat(cutover_str) if cutover_str else None
    entities = payload.get("entities")
    dry_run = bool(payload.get("dry_run", False))

    res = execute_quickbooks_import(
        db=db,
        settings=settings,
        business_id=business_id,
        current=current,
        cutover_date=cutover,
        entities=entities,
        dry_run=dry_run,
        user_id=created_by,
    )
    serialized: dict[str, Any] = {}
    for k, v in res.items():
        if isinstance(v, Decimal):
            serialized[k] = str(v)
        else:
            serialized[k] = v
    return serialized


def handle_report_export(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if business_id is None:
        raise ValueError("Report export requires business_id")
    report_type = payload["report_type"]
    parameters = payload.get("parameters", {})
    fmt = payload.get("format", "json")
    return generate_report_export(db, business_id, report_type, parameters, fmt)


def handle_financial_audit(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if business_id is None or created_by is None:
        raise ValueError("Financial audit job requires business_id and created_by")
    from ledgerpilot.audit.graph import execute_financial_audit
    from ledgerpilot.database import set_context

    set_context(db, created_by, business_id)
    settings = get_settings()
    audit_run_id = UUID(str(payload["audit_run_id"])) if payload.get("audit_run_id") else None
    audit_type = payload.get("audit_type", "emergency")
    req_params = payload.get("request_params") or {}
    res = execute_financial_audit(
        db,
        business_id=business_id,
        actor_id=created_by,
        audit_type=audit_type,
        trigger_source="background_job",
        request_params=req_params,
        settings=settings,
        run_id=audit_run_id,
        persist=True,
    )
    return {
        "audit_run_id": res["id"],
        "status": res["status"],
        "severity_summary": res["severity_summary"],
    }


def register_default_handlers() -> None:
    """Register all standard background job handlers."""
    from ledgerpilot.jobs.worker import register_handler
    from ledgerpilot.observability.janitor import handle_rate_limit_janitor

    register_handler("stripe_webhook", handle_stripe_webhook)
    register_handler("identity_smtp", handle_identity_smtp)
    register_handler("quickbooks_import", handle_quickbooks_import)
    register_handler("report_export", handle_report_export)
    register_handler("financial_audit", handle_financial_audit)
    register_handler("rate_limit_janitor", handle_rate_limit_janitor)
