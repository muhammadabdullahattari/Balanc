"""Background janitor to prune expired rate-limit buckets from PostgreSQL fallback storage."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_
from sqlalchemy.orm import Session

from ledgerpilot.identity.models import AuthRateBucket

logger = logging.getLogger(__name__)


def prune_expired_rate_limit_buckets(db: Session, window_seconds: int = 3600) -> int:
    """Delete expired rate-limit buckets that have outlived their tracking window."""
    now_ts = datetime.now(UTC)
    cutoff = now_ts - timedelta(seconds=window_seconds)

    stmt = delete(AuthRateBucket).where(
        and_(
            AuthRateBucket.started_at < cutoff,
            or_(
                AuthRateBucket.blocked_until.is_(None),
                AuthRateBucket.blocked_until <= now_ts,
            ),
        )
    )
    result = db.execute(stmt)
    deleted_count: int = getattr(result, "rowcount", 0) or 0
    if deleted_count > 0:
        logger.info("Pruned %d expired auth rate-limit buckets", deleted_count)
    return deleted_count


def handle_rate_limit_janitor(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Background worker job handler for periodic rate-limit table maintenance."""
    window_sec = int(payload.get("window_seconds", 3600))
    pruned = prune_expired_rate_limit_buckets(db, window_seconds=window_sec)
    return {"pruned_count": pruned, "window_seconds": window_sec}
