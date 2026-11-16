"""Evaluate a single saved rule against reviewed entries without posting."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, aliased

from ledgerpilot.accounting.models import Account, Journal, JournalLine
from ledgerpilot.audit.service import record
from ledgerpilot.banking.models import BankCorrection, BankRow, BankRule
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Business
from ledgerpilot.tenancy.service import get_business


def evaluate(
    db: Session, bid: UUID, rule: BankRule, start: date, end: date, limit_amount: Decimal
) -> dict[str, Any]:
    if not 0 <= (end - start).days < 93:
        raise DomainError("ERR_DATE_RANGE", "Choose a date range of at most 93 days.", 422)
    limit_amount = ops.money(format(limit_amount, "f"), get_business(db, bid))
    target = ops.account(db, bid, rule.account_id, {"income", "expense"}, plain=True)
    other = aliased(JournalLine)
    reversal = aliased(Journal)
    rows = list(
        db.execute(
            select(BankRow.id, JournalLine.account_id)
            .join(
                Journal,
                (Journal.business_id == BankRow.business_id) & (Journal.id == BankRow.journal_id),
            )
            .join(
                JournalLine,
                (JournalLine.business_id == Journal.business_id)
                & (JournalLine.journal_id == Journal.id),
            )
            .join(
                Account,
                (Account.business_id == JournalLine.business_id)
                & (Account.id == JournalLine.account_id),
            )
            .where(
                BankRow.business_id == bid,
                BankRow.state == "categorized",
                BankRow.accounting_date.between(start, end),
                BankRow.description.icontains(rule.contains, autoescape=True),
                BankRow.amount.between(Decimal(0), limit_amount)
                if target.kind == "income"
                else BankRow.amount.between(-limit_amount, Decimal(0)),
                BankRow.amount != 0,
                Journal.status == "posted",
                JournalLine.account_id != BankRow.account_id,
                JournalLine.debit - JournalLine.credit == -BankRow.amount,
                Account.kind.in_(("income", "expense")),
                Account.control.is_(None),
                ~select(other.position)
                .where(
                    other.business_id == bid,
                    other.journal_id == Journal.id,
                    other.account_id != BankRow.account_id,
                    other.position != JournalLine.position,
                )
                .exists(),
                ~select(reversal.id)
                .where(
                    reversal.business_id == bid,
                    reversal.reversal_of == Journal.id,
                    reversal.status == "posted",
                )
                .exists(),
                ~select(BankCorrection.id)
                .where(
                    BankCorrection.business_id == bid,
                    BankCorrection.row_id == BankRow.id,
                )
                .exists(),
            )
            .order_by(BankRow.id)
            .limit(501)
        )
    )
    if len(rows) > 500:
        raise DomainError("ERR_QUOTA", "Narrow the date range or amount cap to 500 rows.", 413)
    mismatches = [row_id for row_id, actual in rows if actual != rule.account_id]
    return {
        "rule_id": rule.id,
        "rule_version": rule.version,
        "evaluated": len(rows),
        "matched": len(rows) - len(mismatches),
        "mismatched_row_ids": mismatches,
    }


def controlled_posting(
    db: Session,
    bid: UUID,
    who_id: UUID,
    rule: BankRule,
    max_amount: Decimal,
    exclusions: list[str] | None = None,
    paused: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    if paused:
        return {
            "status": "paused",
            "rule_id": rule.id,
            "posted_count": 0,
            "eligible_count": 0,
            "flagged_amount_count": 0,
            "flagged_exclusion_count": 0,
            "flagged_amount_row_ids": [],
            "flagged_exclusion_row_ids": [],
            "posted_row_ids": [],
        }

    target = ops.account(db, bid, rule.account_id, {"income", "expense"}, plain=True)
    limit_amount = ops.money(format(max_amount, "f"), get_business(db, bid))
    active_exclusions = [
        e.lower().strip() for e in (exclusions or ["payroll", "tax", "wire", "loan", "draw"])
    ]

    candidate_rows = list(
        db.scalars(
            select(BankRow)
            .where(
                BankRow.business_id == bid,
                BankRow.state == "imported",
                BankRow.description.icontains(rule.contains, autoescape=True),
                BankRow.amount != 0,
            )
            .order_by(BankRow.accounting_date, BankRow.id)
            .limit(100)
        )
    )

    eligible_rows: list[BankRow] = []
    flagged_amount: list[UUID] = []
    flagged_exclusions: list[UUID] = []

    for row in candidate_rows:
        row_mag = abs(row.amount)
        if row_mag > limit_amount:
            flagged_amount.append(row.id)
            continue
        desc_lower = row.description.lower()
        if any(ex in desc_lower for ex in active_exclusions):
            flagged_exclusions.append(row.id)
            continue
        if (target.kind == "income" and row.amount < 0) or (
            target.kind == "expense" and row.amount > 0
        ):
            continue
        eligible_rows.append(row)

    posted_ids: list[UUID] = []
    if not dry_run and eligible_rows:
        for row in eligible_rows:
            debit = abs(row.amount) if row.amount < 0 else Decimal(0)
            credit = abs(row.amount) if row.amount > 0 else Decimal(0)
            b_row = (
                db.execute(
                    select(Business.version, Business.currency)
                    .where(Business.id == bid)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            key = f"auto-post:{rule.id}:{row.id}"
            txn_date = str(row.accounting_date)
            fingerprint = hashlib.sha256(f"{key}:{txn_date}:{row.amount}".encode()).hexdigest()
            lines = [
                {
                    "account_id": str(rule.account_id),
                    "position": 1,
                    "debit": f"{debit:.2f}",
                    "credit": f"{credit:.2f}",
                },
                {
                    "account_id": str(row.account_id),
                    "position": 2,
                    "debit": f"{credit:.2f}",
                    "credit": f"{debit:.2f}",
                },
            ]
            journal_id: UUID = db.execute(
                text("""
                    SELECT app.post_journal(
                        :b, :date, :memo, :currency, :key, :hash, :version,
                        CAST(:lines AS jsonb), NULL
                    )
                """),
                {
                    "b": bid,
                    "date": txn_date,
                    "memo": f"Controlled auto-post: {rule.name or rule.contains}",
                    "currency": b_row["currency"],
                    "key": key,
                    "hash": fingerprint,
                    "version": b_row["version"],
                    "lines": json.dumps(lines),
                },
            ).scalar_one()

            row.journal_id = journal_id
            row.state = "categorized"
            posted_ids.append(row.id)
            record(
                db,
                who_id,
                "bank.auto_posted",
                bid,
                row.id,
                {"rule_id": str(rule.id), "journal_id": str(journal_id), "amount": str(row.amount)},
            )

    return {
        "status": "dry_run" if dry_run else "posted",
        "rule_id": rule.id,
        "eligible_count": len(eligible_rows),
        "posted_count": len(posted_ids),
        "flagged_amount_count": len(flagged_amount),
        "flagged_exclusion_count": len(flagged_exclusions),
        "flagged_amount_row_ids": flagged_amount,
        "flagged_exclusion_row_ids": flagged_exclusions,
        "posted_row_ids": posted_ids,
    }
