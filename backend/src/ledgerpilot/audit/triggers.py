"""Auto-trigger hooks for balance sheet generation and period close/finalization."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from ledgerpilot.audit.graph import execute_financial_audit
from ledgerpilot.audit.state import AuditMode, TriggerSource
from ledgerpilot.config import Settings, get_settings

logger = logging.getLogger("ledgerpilot.operations")


def trigger_auto_audit(
    db: Session,
    *,
    business_id: UUID,
    actor_id: UUID,
    audit_type: AuditMode,
    trigger_source: TriggerSource,
    start_date: date,
    end_date: date,
    trigger_resource_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Run an automatic read-only consistency audit inside a savepoint.

    Uses a nested transaction so missing optional tables in isolated unit-test stubs
    never disrupt primary report or close operations, while real PostgreSQL sessions
    persist the full FinancialAuditRun record under RLS.
    """
    cfg = settings or get_settings()
    try:
        with db.begin_nested():
            res = execute_financial_audit(
                db,
                business_id=business_id,
                actor_id=actor_id,
                audit_type=audit_type,
                trigger_source=trigger_source,
                request_params={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
                scope_filters=(
                    {"trigger_resource_id": trigger_resource_id} if trigger_resource_id else {}
                ),
                settings=cfg,
                persist=True,
            )
            return res
    except Exception as exc:
        logger.debug("Auto-triggered audit skipped (%s): %s", trigger_source, type(exc).__name__)
        return None
