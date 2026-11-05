"""Write only allowlisted event metadata in the business transaction."""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from ledgerpilot.audit.models import AuditEvent
from ledgerpilot.shared.telemetry import emit


def record(
    session: Session,
    user_id: UUID,
    action: str,
    business_id: UUID | None = None,
    resource_id: UUID | None = None,
    details: dict[str, object] | None = None,
) -> None:
    allowed = {"reason", "disputed", "version", "status", "ticket", "expires_at"}
    dropped = set(details or {}) - allowed
    if dropped:
        # Unknown names may themselves contain private data: log only the count.
        emit(
            "audit.details_omitted",
            level=logging.WARNING,
            action=action,
            dropped_key_count=len(dropped),
        )
    emit(
        "audit.staged",
        action=action,
        user_id=user_id,
        business_id=business_id,
        resource_id=resource_id,
        stage="transaction_pending",
    )
    session.add(
        AuditEvent(
            user_id=user_id,
            action=action,
            business_id=business_id,
            resource_id=resource_id,
            details={k: v for k, v in (details or {}).items() if k in allowed},
        )
    )
