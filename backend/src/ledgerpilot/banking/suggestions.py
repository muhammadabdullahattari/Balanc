"""Repeated reviewed corrections suggest a draft rule, never activate it."""

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account, Journal
from ledgerpilot.banking.models import BankCorrection, BankRow
from ledgerpilot.documents.duplicates import normalize


def correction_rules(db: Session, bid: UUID) -> dict[str, Any]:
    rows = db.execute(
        select(BankCorrection, BankRow.description)
        .join(
            BankRow,
            (BankRow.business_id == BankCorrection.business_id)
            & (BankRow.id == BankCorrection.row_id),
        )
        .join(
            Journal,
            (Journal.business_id == BankCorrection.business_id)
            & (Journal.id == BankCorrection.journal_id),
        )
        .where(BankCorrection.business_id == bid)
        .order_by(Journal.posted_at.desc(), BankCorrection.id.desc())
        .limit(10001)
    )
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    active = {
        str(a)
        for a in db.scalars(
            select(Account.id).where(
                Account.business_id == bid,
                Account.active.is_(True),
                Account.control.is_(None),
                Account.kind.in_(["income", "expense"]),
            )
        )
    }
    descriptions: dict[str, str] = {}
    seen: set[UUID] = set()
    for correction, description in rows:
        if correction.row_id in seen:
            continue
        seen.add(correction.row_id)
        normalized = normalize(description)
        if len(correction.splits) != 1 or len(normalized) < 2:
            continue
        key = (normalized, correction.splits[0]["account_id"])
        if key[1] not in active:
            continue
        descriptions.setdefault(normalized, description[:120])
        groups[key].append(str(correction.id))
    return {
        "items": [
            {
                "descriptor": merchant,
                "suggested_contains": descriptions[merchant],
                "account_id": account,
                "correction_ids": ids,
                "sample_count": len(ids),
                "requires_preview_and_approval": True,
                "active": False,
            }
            for (merchant, account), ids in sorted(groups.items())
            if len(ids) >= 2
        ],
        "minimum_distinct_transactions": 2,
        "automatic_activation": False,
    }
