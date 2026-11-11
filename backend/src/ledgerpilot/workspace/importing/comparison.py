"""Compare imported and parallel-run balances against the posted general ledger."""

import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account
from ledgerpilot.reports.service import balances, controls
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.query_limits import bounded_scalars
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.workspace.importing.schemas import ParallelTrialBalance
from ledgerpilot.workspace.models import ImportBatch


def compare(
    db: Session, bid: UUID, batch_id: UUID, parallel: ParallelTrialBalance | None = None
) -> dict[str, Any]:
    item = db.scalar(
        select(ImportBatch).where(ImportBatch.business_id == bid, ImportBatch.id == batch_id)
    )
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Import batch not found.", 404)
    if item.mode == "chart":
        return {
            "batch_id": str(item.id),
            "comparison_available": False,
            "reason": "Chart-only imports have no financial baseline.",
        }
    as_of = item.cutover_date
    accounts = bounded_scalars(
        db, select(Account).where(Account.business_id == bid).order_by(Account.code)
    )
    expected = {code: Decimal(value) for code, value in item.expected_trial.items()}
    if parallel is not None:
        as_of = parallel.as_of
        if as_of < item.cutover_date:
            raise DomainError("ERR_DATE_RANGE", "Choose a date on or after the import cutover.")
        business = get_business(db, bid)
        known_codes = {account.code for account in accounts}
        expected = {}
        for code, value in parallel.expected_trial.items():
            if code not in known_codes or not re.fullmatch(
                r"-?[0-9]{1,18}(?:\.[0-9]{1,6})?", value
            ):
                raise DomainError(
                    "ERR_EXPECTED_TOTAL",
                    "Use existing account codes and signed decimal balances.",
                )
            amount = Decimal(value)
            ops.money(format(abs(amount), "f"), business, positive=False)
            expected[code] = amount
        if sum(expected.values()) != 0:
            raise DomainError("ERR_UNBALANCED", "The source trial balance must net to zero.")

    actual = balances(db, bid, as_of)
    rows = []
    for account in accounts:
        legacy = expected.get(account.code, Decimal(0))
        current = actual.get(account.id, Decimal(0))
        rows.append(
            {
                "account_id": str(account.id),
                "code": account.code,
                "expected": str(legacy),
                "actual": str(current),
                "difference": str(current - legacy),
            }
        )
    return {
        "batch_id": str(item.id),
        "as_of": as_of,
        "items": rows,
        "balanced": all(Decimal(row["difference"]) == 0 for row in rows),
        "controls": controls(db, bid, as_of),
        "basis": "accrual_general_ledger_baseline",
    }
