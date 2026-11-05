"""Explicit pilot report bounds; never return silently truncated financial data."""

from sqlalchemy import Row, Select
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError

REPORT_LIMIT = 10000


def bounded_scalars[T](db: Session, query: Select[T]) -> list[T]:
    values = list(db.scalars(query.limit(REPORT_LIMIT + 1)))
    if len(values) > REPORT_LIMIT:
        raise DomainError(
            "ERR_QUOTA", "Narrow the pilot report scope or request an export job.", 413
        )
    return values


def bounded_rows[*Ts](db: Session, query: Select[*Ts]) -> list[Row[*Ts]]:
    values = list(db.execute(query.limit(REPORT_LIMIT + 1)))
    if len(values) > REPORT_LIMIT:
        raise DomainError(
            "ERR_QUOTA", "Narrow the pilot report scope or request an export job.", 413
        )
    return values
