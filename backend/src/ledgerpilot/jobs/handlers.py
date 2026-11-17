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


def register_default_handlers() -> None:
    """Register all standard background job handlers."""
    from ledgerpilot.jobs.worker import register_handler
    from ledgerpilot.observability.janitor import handle_rate_limit_janitor

    register_handler("stripe_webhook", handle_stripe_webhook)
    register_handler("identity_smtp", handle_identity_smtp)
    register_handler("quickbooks_import", handle_quickbooks_import)
    register_handler("report_export", handle_report_export)
    register_handler("rate_limit_janitor", handle_rate_limit_janitor)
