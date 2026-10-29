"""Manual bank import, matching and disposition with no implicit classification."""

import csv
import hashlib
import io
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Journal, JournalLine, Period
from ledgerpilot.audit.service import record
from ledgerpilot.banking.models import BankImport, BankMatch, BankRow, BankRule
from ledgerpilot.banking.schemas import BankDisposition, CSVImport, CSVPreview
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Business
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import Operation
from ledgerpilot.workspace.models import TrialEntitlement


def import_limit(db: Session, bid: UUID, result: dict[str, Any]) -> None:
    trial = db.scalar(select(TrialEntitlement).where(TrialEntitlement.business_id == bid))
    maximum = min(500, trial.import_row_limit if trial else 500)
    if len(result["rows"]) + len(result["errors"]) > maximum:
        raise DomainError("ERR_QUOTA", "Bank import exceeds this business's row limit.", 413)


def preview(data: CSVPreview, business: Business) -> dict[str, Any]:
    digest = hashlib.sha256(data.content.encode()).hexdigest()
    source = csv.DictReader(io.StringIO(data.content.lstrip("\ufeff")))
    required = [
        data.mapping.date_column,
        data.mapping.amount_column,
        data.mapping.description_column,
    ]
    if data.mapping.external_id_column:
        required.append(data.mapping.external_id_column)
    if (
        not source.fieldnames
        or len(source.fieldnames) != len(set(source.fieldnames))
        or not set(required) <= set(source.fieldnames)
        or len(source.fieldnames) > 50
    ):
        raise DomainError("ERR_CSV_MAPPING", "Mapping requires unique CSV headers.")
    rows, errors = [], []
    for position, row in enumerate(source, 1):
        if position > 500:
            raise DomainError("ERR_QUOTA", "The local bank import cap is 500 rows.", 413)
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Column count does not match headers")
            raw_amount = row[data.mapping.amount_column].strip()
            if not re.fullmatch(r"-?[0-9]{1,18}(?:\.[0-9]{1,6})?", raw_amount):
                raise ValueError("Use a signed exact decimal without currency symbols")
            amount = Decimal(raw_amount)
            ops.money(format(abs(amount), "f"), business)
            day = datetime.strptime(row[data.mapping.date_column].strip(), data.mapping.date_format)
            description = row[data.mapping.description_column]
            external = (
                row[data.mapping.external_id_column] if data.mapping.external_id_column else None
            )
            if len(description) > 1000 or (external and len(external) > 160):
                raise ValueError("Description or source identifier is too long")
            rows.append(
                {
                    "position": position,
                    "accounting_date": day.date().isoformat(),
                    "description": description,
                    "amount": str(amount),
                    "raw": row,
                    "external_id": external or None,
                }
            )
        except (ValueError, DomainError):
            errors.append(
                {
                    "row": position,
                    "code": "ERR_CSV_ROW",
                    "message": "Check mapped columns, date, exact amount and field lengths.",
                }
            )
    if not rows and not errors:
        raise DomainError("ERR_CSV_EMPTY", "CSV must contain transaction rows.")
    return {"file_hash": digest, "rows": rows, "errors": errors, "can_import": not errors}


def import_csv(
    db: Session,
    who: Principal,
    bid: UUID,
    data: CSVImport,
) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    business = get_business(db, bid, lock=True)
    ops.account(db, bid, data.account_id, {"asset", "liability"}, plain=True)
    result = preview(data, business)
    import_limit(db, bid, result)
    if data.expected_hash != result["file_hash"]:
        raise DomainError("ERR_CSV_CHANGED", "Preview hash does not match the submitted file.", 409)
    old = db.scalar(
        select(BankImport).where(
            BankImport.business_id == bid,
            BankImport.account_id == data.account_id,
            BankImport.file_hash == result["file_hash"],
        )
    )
    if old:
        if old.mapping != data.mapping.model_dump():
            raise DomainError(
                "ERR_IDEMPOTENCY_CONFLICT", "File was imported with another mapping.", 409
            )
        return {"id": str(old.id), "reimport": True, "created": 0}
    if result["errors"]:
        raise DomainError("ERR_CSV_ROWS", "Correct preview errors before importing.")
    item = BankImport(
        business_id=bid,
        account_id=data.account_id,
        file_hash=result["file_hash"],
        mapping=data.mapping.model_dump(),
        row_count=len(result["rows"]),
    )
    db.add(item)
    db.flush()
    created = 0
    source_ids: set[str] = set()
    for row in result["rows"]:
        external_id = row["external_id"]
        if external_id:
            if external_id in source_ids:
                raise DomainError(
                    "ERR_SOURCE_DUPLICATE", "Duplicate source identifier within file."
                )
            source_ids.add(external_id)
            existing = db.scalar(
                select(BankRow).where(
                    BankRow.business_id == bid,
                    BankRow.account_id == data.account_id,
                    BankRow.external_id == external_id,
                )
            )
            if existing:
                if (
                    existing.accounting_date.isoformat() != row["accounting_date"]
                    or existing.amount != Decimal(row["amount"])
                    or existing.description != row["description"]
                ):
                    raise DomainError(
                        "ERR_SOURCE_CONFLICT", "Source ID has inconsistent content.", 409
                    )
                continue
        db.add(
            BankRow(
                business_id=bid,
                import_id=item.id,
                account_id=data.account_id,
                position=row["position"],
                accounting_date=datetime.fromisoformat(row["accounting_date"]).date(),
                description=row["description"],
                amount=Decimal(row["amount"]),
                raw=row["raw"],
                external_id=external_id,
            )
        )
        created += 1
    record(db, who.user.id, "bank.imported", bid, item.id)
    return {"id": str(item.id), "reimport": False, "created": created}


def bank_row(db: Session, bid: UUID, rid: UUID) -> BankRow:
    row = db.scalar(select(BankRow).where(BankRow.business_id == bid, BankRow.id == rid))
    if not row:
        raise DomainError("ERR_NOT_FOUND", "Bank transaction not found.", 404)
    return row


def dispose(
    db: Session,
    who: Principal,
    bid: UUID,
    rid: UUID,
    data: BankDisposition,
    *,
    preview_only: bool = False,
) -> dict[str, Any]:
    if preview_only:
        authorize(db, who, bid, {"owner", "accountant", "bookkeeper", "reviewer"})
        business, old = get_business(db, bid), None
    else:
        business, old = ops.begin(db, who, bid, data, f"bank.{rid}.disposed")
    if old is not None:
        return old
    row = bank_row(db, bid, rid)
    if row.version != data.expected_row_version or row.state != "for_review":
        raise DomainError("ERR_VERSION", "Transaction is already disposed or changed.", 409)
    amount, incoming = abs(row.amount), row.amount > 0
    source_account = ops.account(db, bid, row.account_id, {"asset", "liability"}, plain=True)
    base_preview = {
        "posted": False,
        "currency": business.currency,
        "timezone": business.timezone,
        "business_version": business.version,
        "row_version": row.version,
    }
    journal_id = None
    late_peer = False
    consumed: list[UUID] = []
    if data.action == "exclude":
        if (
            data.splits
            or data.peer_id
            or data.journal_id
            or data.journal_ids
            or data.target_account_id
        ):
            raise DomainError(
                "ERR_DISPOSITION", "Exclusion cannot also contain posting instructions."
            )
        if preview_only:
            return {**base_preview, "action": "exclude", "lines": []}
        row.state = "excluded"
    elif data.action == "match":
        if (
            bool(data.journal_id) == bool(data.journal_ids)
            or data.splits
            or data.peer_id
            or data.target_account_id
        ):
            raise DomainError("ERR_MATCH", "Select one journal or one explicit journal bundle.")
        consumed = [data.journal_id] if data.journal_id else data.journal_ids
        journals = list(
            db.scalars(
                select(Journal).where(
                    Journal.business_id == bid,
                    Journal.id.in_(consumed),
                    Journal.status == "posted",
                    Journal.accounting_date <= row.accounting_date,
                )
            )
        )
        movements = dict(
            db.execute(
                select(JournalLine.journal_id, func.sum(JournalLine.debit - JournalLine.credit))
                .where(
                    JournalLine.business_id == bid,
                    JournalLine.journal_id.in_(consumed),
                    JournalLine.account_id == row.account_id,
                )
                .group_by(JournalLine.journal_id)
            ).all()
        )
        if len(journals) != len(consumed) or any(not movements.get(j) for j in consumed):
            raise DomainError(
                "ERR_MATCH", "Every selected source must have a compatible posted bank movement."
            )
        if sum(movements.values(), Decimal(0)) != row.amount:
            raise DomainError(
                "ERR_MATCH", "The selected journal bundle must equal the bank movement."
            )
        already = db.scalar(
            select(BankRow.id).where(
                BankRow.business_id == bid,
                BankRow.account_id == row.account_id,
                BankRow.journal_id.in_(consumed),
            )
        )
        already = already or db.scalar(
            select(BankMatch.id).where(
                BankMatch.business_id == bid,
                BankMatch.account_id == row.account_id,
                BankMatch.journal_id.in_(consumed),
            )
        )
        if already:
            raise DomainError(
                "ERR_MATCH", "A selected bank movement has already been consumed.", 409
            )
        if preview_only:
            return {
                **base_preview,
                "action": "match",
                "lines": [],
                "matched_journal_ids": [str(j) for j in consumed],
            }
        journal_id, row.state = consumed[0], "matched"
    else:
        if data.journal_id or data.journal_ids:
            raise DomainError(
                "ERR_DISPOSITION", "Posting actions cannot also match existing journals."
            )
        lines = [ops.line(row.account_id, amount, debit=incoming)]
        if data.action in {"transfer", "card_payment"}:
            if (
                not data.target_account_id
                or data.target_account_id == row.account_id
                or data.splits
            ):
                raise DomainError("ERR_TRANSFER", "A separate target account is required.")
            target = ops.account(
                db,
                bid,
                data.target_account_id,
                {"liability"}
                if data.action == "card_payment" and source_account.kind == "asset"
                else {"asset"},
                control="clearing"
                if data.action == "transfer" and (not data.peer_id or data.allow_clearing)
                else None,
                plain=not (data.action == "transfer" and (not data.peer_id or data.allow_clearing)),
            )
            if data.action == "card_payment" and incoming != (source_account.kind == "liability"):
                raise DomainError(
                    "ERR_TRANSFER",
                    "A card settlement must debit the liability and credit the cash account.",
                )
            if data.action == "transfer" and source_account.kind != "asset":
                raise DomainError(
                    "ERR_TRANSFER", "Use card-payment disposition for credit-card liabilities."
                )
            if data.peer_id:
                peer = bank_row(db, bid, data.peer_id)
                late_peer = data.action == "transfer" and data.allow_clearing
                if late_peer:
                    original = db.scalar(
                        select(Operation).where(
                            Operation.business_id == bid,
                            Operation.action == f"bank.{peer.id}.disposed",
                        )
                    )
                    clearing = db.scalar(
                        select(func.sum(JournalLine.debit - JournalLine.credit)).where(
                            JournalLine.business_id == bid,
                            JournalLine.journal_id == peer.journal_id,
                            JournalLine.account_id == target.id,
                        )
                    )
                    linked = db.scalar(
                        select(BankRow.id).where(
                            BankRow.business_id == bid, BankRow.peer_id == peer.id
                        )
                    )
                    if (
                        not original
                        or original.result.get("disposition") != "transfer"
                        or peer.state != "categorized"
                        or peer.peer_id is not None
                        or linked is not None
                        or peer.account_id == row.account_id
                        or peer.id == row.id
                        or peer.amount != -row.amount
                        or clearing != row.amount
                        or peer.accounting_date > row.accounting_date
                    ):
                        raise DomainError(
                            "ERR_TRANSFER",
                            "Select the unlinked earlier transfer into this clearing account.",
                        )
                elif (
                    peer.account_id != target.id
                    or peer.amount != -row.amount
                    or peer.state != "for_review"
                    or peer.id == row.id
                    or peer.accounting_date != row.accounting_date
                ):
                    raise DomainError(
                        "ERR_TRANSFER", "Peer must be the unconsumed opposite same-day bank side."
                    )
            elif data.action == "transfer" and not data.allow_clearing:
                raise DomainError(
                    "ERR_TRANSFER", "Select the counterpart or explicitly approve clearing."
                )
            # Single-side transfers require an explicitly chosen clearing account;
            # later disposition moves that clearing balance into the receiving bank.
            lines.append(ops.line(target.id, amount, debit=not incoming))
        else:
            if data.peer_id or data.journal_id or data.target_account_id:
                raise DomainError("ERR_DISPOSITION", "Categorization requires split lines only.")
            if not data.splits or (data.action == "categorize" and len(data.splits) != 1):
                raise DomainError("ERR_SPLIT", "Supply the explicit category allocations.")
            total = Decimal(0)
            for split in data.splits:
                if split.account_id == row.account_id:
                    raise DomainError("ERR_SPLIT", "Category cannot equal the bank account.")
                # AR/AP/loans require the source modules to keep control tie-outs intact.
                ops.account(
                    db,
                    bid,
                    split.account_id,
                    {"asset", "liability", "equity", "income", "expense"},
                    plain=True,
                )
                value = ops.money(split.amount, business)
                total += value
                lines.append(ops.line(split.account_id, value, debit=not incoming))
            if total != amount:
                raise DomainError("ERR_SPLIT", "Split total must equal the bank amount exactly.")
        if preview_only:
            period = db.scalar(
                select(Period.id).where(
                    Period.business_id == bid,
                    Period.start_date <= row.accounting_date,
                    Period.end_date >= row.accounting_date,
                    Period.locked.is_(False),
                )
            )
            return {
                **base_preview,
                "action": data.action,
                "period_open": period is not None,
                "lines": [line.model_dump(mode="json") for line in lines],
            }
        journal_id = ops.publish(db, who, business, data, row.accounting_date, data.reason, lines)
        consumed = [journal_id]
        row.state = "categorized"
        if data.peer_id:
            row.peer_id = peer.id
            if not late_peer:
                peer.peer_id = row.id
                peer.version += 1
                peer.journal_id, peer.state, peer.reason = journal_id, "categorized", data.reason
                db.add(
                    BankMatch(
                        business_id=bid,
                        row_id=peer.id,
                        account_id=peer.account_id,
                        journal_id=journal_id,
                    )
                )
    row.journal_id, row.reason = journal_id, data.reason
    row.version += 1
    for jid in consumed:
        db.add(BankMatch(business_id=bid, row_id=row.id, account_id=row.account_id, journal_id=jid))
    return ops.finish(
        db,
        who,
        bid,
        data,
        f"bank.{rid}.disposed",
        {
            "id": str(row.id),
            "state": row.state,
            "disposition": data.action,
            "version": row.version,
            "journal_id": str(journal_id) if journal_id else None,
        },
        journal_id if data.action not in {"match", "exclude"} else None,
    )


def rule_preview(db: Session, bid: UUID, rule: BankRule) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(BankRow)
            .where(
                BankRow.business_id == bid,
                BankRow.state == "for_review",
                BankRow.description.icontains(rule.contains, autoescape=True),
            )
            .order_by(BankRow.id)
            .limit(501)
        )
    )
    if len(rows) > 500:
        raise DomainError("ERR_QUOTA", "Narrow the rule to preview at most 500 transactions.")
    payload = {
        "rule_id": str(rule.id),
        "version": rule.version,
        "account_id": str(rule.account_id),
        "contains": rule.contains,
        "rows": [{"id": str(r.id), "version": r.version, "amount": str(r.amount)} for r in rows],
    }
    return {
        **payload,
        "preview_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    }
