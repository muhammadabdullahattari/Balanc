"""Write only allowlisted event metadata in the business transaction."""

from uuid import UUID

from sqlalchemy.orm import Session

from ledgerpilot.audit.models import AuditEvent


def record(
    session: Session,
    user_id: UUID,
    action: str,
    business_id: UUID | None = None,
    resource_id: UUID | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            user_id=user_id,
            action=action,
            business_id=business_id,
            resource_id=resource_id,
            details={
                k: v
                for k, v in (details or {}).items()
                if k in {"reason", "disputed", "version", "status", "ticket", "expires_at"}
            },
        )
    )
