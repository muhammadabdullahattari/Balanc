"""Evaluate a single saved rule against reviewed entries without posting."""

import datetime
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
from ledgerpilot.banking.models import BankCorrection, BankMatch, BankRow, BankRule
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Business
from ledgerpilot.tenancy.service import get_business


def get_workspace_posting_control(db: Session, bid: UUID) -> dict[str, Any]:
    try:
        row = (
            db.execute(
                text("""
                SELECT action, user_id, occurred_at, details
                FROM app.audit_events
                WHERE business_id = :b
                  AND action IN ('workspace.auto_posting_paused', 'workspace.auto_posting_resumed')
                ORDER BY occurred_at DESC, id DESC
                LIMIT 1
            """),
                {"b": bid},
            )
            .mappings()
            .first()
        )
        if row is not None:
            details = row["details"] or {}
            is_paused = row["action"] == "workspace.auto_posting_paused"
            return {
                "paused": is_paused,
                "reason": details.get("reason") if is_paused else None,
                "updated_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                "updated_by": str(row["user_id"]),
            }
    except Exception:
        pass
    return {"paused": False, "reason": None, "updated_at": None, "updated_by": None}


def set_workspace_posting_control(
    db: Session,
    bid: UUID,
    who_id: UUID,
    paused: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    record(
        db,
        who_id,
        "workspace.auto_posting_paused" if paused else "workspace.auto_posting_resumed",
        bid,
        bid,
        {"reason": reason},
    )
    return {
        "paused": paused,
        "reason": reason if paused else None,
        "updated_at": now_iso,
        "updated_by": str(who_id),
    }


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
    expected_rule_version: int | None = None,
    start: date | None = None,
    end: date | None = None,
    exclusions: list[str] | None = None,
    paused: bool = False,
    dry_run: bool = True,
    expected_preview_hash: str | None = None,
) -> dict[str, Any]:
    workspace_ctrl = get_workspace_posting_control(db, bid)
    if workspace_ctrl.get("paused"):
        if not dry_run:
            reason = workspace_ctrl.get("reason") or "Paused by operator"
            raise DomainError(
                "ERR_PAUSED",
                f"Controlled auto-posting is durably paused for this workspace: {reason}.",
                409,
            )
        return {
            "status": "workspace_paused",
            "rule_id": rule.id,
            "rule_version": rule.version,
            "rule_active": rule.active,
            "reason": workspace_ctrl.get("reason"),
            "posted_count": 0,
            "eligible_count": 0,
            "flagged_amount_count": 0,
            "flagged_exclusion_count": 0,
            "flagged_amount_row_ids": [],
            "flagged_exclusion_row_ids": [],
            "posted_row_ids": [],
        }

    if paused:
        return {
            "status": "paused",
            "rule_id": rule.id,
            "rule_version": rule.version,
            "rule_active": rule.active,
            "posted_count": 0,
            "eligible_count": 0,
            "flagged_amount_count": 0,
            "flagged_exclusion_count": 0,
            "flagged_amount_row_ids": [],
            "flagged_exclusion_row_ids": [],
            "posted_row_ids": [],
        }

    if expected_rule_version is not None and rule.version != expected_rule_version:
        raise DomainError(
            "ERR_VERSION",
            f"Rule version mismatch (expected {expected_rule_version}, current {rule.version}).",
            409,
        )

    if not dry_run and expected_rule_version is None:
        raise DomainError(
            "ERR_VERSION_REQUIRED",
            "Live automated posting requires expected_rule_version matching reviewed rule version.",
            422,
        )

    if (not rule.active or not rule.preview_hash) and not dry_run:
        raise DomainError(
            "ERR_RULE_INACTIVE",
            "Rule must be active and approved before automated posting can execute.",
            409,
        )

    target = ops.account(db, bid, rule.account_id, {"income", "expense"}, plain=True)
    limit_amount = ops.money(format(max_amount, "f"), get_business(db, bid))
    active_exclusions = [
        e.lower().strip() for e in (exclusions or ["payroll", "tax", "wire", "loan", "draw"])
    ]

    stmt = select(BankRow).where(
        BankRow.business_id == bid,
        BankRow.state == "for_review",
        BankRow.description.icontains(rule.contains, autoescape=True),
        BankRow.amount != 0,
    )
    if start is not None:
        stmt = stmt.where(BankRow.accounting_date >= start)
    if end is not None:
        stmt = stmt.where(BankRow.accounting_date <= end)
    stmt = stmt.order_by(BankRow.accounting_date, BankRow.id).limit(100)

    if not dry_run:
        stmt = stmt.with_for_update()
    candidate_rows = list(db.scalars(stmt))

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

    preview_hash = hashlib.sha256(
        json.dumps(
            [
                str(rule.id),
                rule.version,
                str(start),
                str(end),
                str(limit_amount),
                active_exclusions,
                [
                    [str(row.id), str(row.amount), str(row.accounting_date), str(row.account_id)]
                    for row in eligible_rows
                ],
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if not dry_run and expected_preview_hash is not None and expected_preview_hash != preview_hash:
        raise DomainError("ERR_PREVIEW_CHANGED", "Candidates changed. Run a new dry run.", 409)

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
                    "debit": format(debit, "f"),
                    "credit": format(credit, "f"),
                },
                {
                    "account_id": str(row.account_id),
                    "position": 2,
                    "debit": format(credit, "f"),
                    "credit": format(debit, "f"),
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
            row.reason = "Controlled posting using the approved bank rule."
            row.version += 1
            db.add(
                BankMatch(
                    business_id=bid, row_id=row.id, account_id=row.account_id, journal_id=journal_id
                )
            )
            posted_ids.append(row.id)
            record(
                db,
                who_id,
                "bank.auto_posted",
                bid,
                row.id,
                {
                    "rule_id": str(rule.id),
                    "rule_version": rule.version,
                    "journal_id": str(journal_id),
                    "amount": str(row.amount),
                    "scope": {
                        "start": str(start) if start else None,
                        "end": str(end) if end else None,
                        "max_amount": str(limit_amount),
                    },
                    "exclusions": active_exclusions,
                },
            )

    return {
        "status": "dry_run" if dry_run else "posted",
        "rule_id": rule.id,
        "rule_version": rule.version,
        "rule_active": rule.active,
        "scope": {
            "start": str(start) if start else None,
            "end": str(end) if end else None,
            "max_amount": str(limit_amount),
        },
        "eligible_count": len(eligible_rows),
        "preview_hash": preview_hash,
        "candidates": [
            {
                "id": str(row.id),
                "date": str(row.accounting_date),
                "description": row.description,
                "amount": str(row.amount),
            }
            for row in eligible_rows
        ]
        if dry_run
        else [],
        "posted_count": len(posted_ids),
        "flagged_amount_count": len(flagged_amount),
        "flagged_exclusion_count": len(flagged_exclusions),
        "flagged_amount_row_ids": flagged_amount,
        "flagged_exclusion_row_ids": flagged_exclusions,
        "posted_row_ids": posted_ids,
    }
