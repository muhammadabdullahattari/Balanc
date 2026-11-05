"""Reviewed, bounded CSV imports compile to the same public accounting workflows.

No missing source dates, counterparties, categories or historical allocations are
invented. GL control accounts are accepted only when final subledgers tie out.
"""

import csv
import io
import json
import re
from contextlib import suppress
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account
from ledgerpilot.accounting.schemas import AccountCreate, LineCreate
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade.models import Party
from ledgerpilot.trade.schemas import DocumentCreate, LoanCreate, LoanMovementCreate, PaymentCreate
from ledgerpilot.trade.service import (
    document_impact,
)
from ledgerpilot.workspace import openings
from ledgerpilot.workspace.importing.schemas import ImportJournal, ImportPreview


def source_rows(data: ImportPreview) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(data.content.lstrip("\ufeff")))
    if (
        not reader.fieldnames
        or len(reader.fieldnames) != len(set(reader.fieldnames))
        or len(reader.fieldnames) > 30
    ):
        raise DomainError("ERR_CSV_MAPPING", "Import requires unique CSV headers.")
    if any(source not in reader.fieldnames for source in data.columns.values()):
        raise DomainError("ERR_CSV_MAPPING", "Mapped import columns are missing.")
    rows: list[dict[str, str]] = []
    for row in reader:
        if len(rows) >= 100:
            raise DomainError("ERR_QUOTA", "Pilot imports support at most 100 CSV rows.", 413)
        if None in row or any(value is None for value in row.values()):
            raise DomainError("ERR_CSV_ROW", "CSV column counts must match headers.")
        rows.append(
            {key: row[source].strip() for key, source in data.columns.items()}
            if data.columns
            else {key: value.strip() for key, value in row.items()}
        )
    if not rows:
        raise DomainError("ERR_CSV_EMPTY", "Import contains no rows.")
    return rows


def compile_rows(
    db: Session,
    bid: UUID,
    data: ImportPreview,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    business = get_business(db, bid)
    accounts = {a.code: a for a in db.scalars(select(Account).where(Account.business_id == bid))}
    rows = source_rows(data)
    account_ids = {a.id: a for a in accounts.values()}
    party_ids = set()
    for row in rows:
        # The row validator below records malformed/missing identifiers.
        with suppress(ValueError):
            party_ids.add(UUID(row.get("party_id", "")))
    parties = (
        {
            p.id: p
            for p in db.scalars(
                select(Party).where(Party.business_id == bid, Party.id.in_(party_ids))
            )
        }
        if party_ids
        else {}
    )
    commands: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    seen_codes: set[str] = set()
    opening_refs: set[tuple[str, UUID, str]] = set()
    if data.mode == "opening":
        from ledgerpilot.accounting.models import Journal

        if db.scalar(
            select(Journal.id)
            .where(Journal.business_id == bid, Journal.status == "posted")
            .limit(1)
        ):
            errors.append(
                {
                    "row": 0,
                    "code": "ERR_OPENING_STATE",
                    "message": "Opening balances require books with no posted journals.",
                }
            )
        if not data.opening_equity_account_code or not (data.review_reason or "").strip():
            errors.append(
                {
                    "row": 0,
                    "code": "ERR_OPENING_REVIEW",
                    "message": "Select opening equity and supply the accountant's reason.",
                }
            )
    for position, row in enumerate(rows, 1):
        try:
            if row.get("currency") and row["currency"] != business.currency:
                raise ValueError("Foreign currency is unsupported")
            if data.mode == "opening":
                equity = accounts[data.opening_equity_account_code or ""]
                opening_row = openings.OpeningRow.model_validate(
                    {
                        "kind": row["kind"],
                        "party_id": row["party_id"],
                        "reference": row["reference"],
                        "original_date": row["original_date"],
                        "due_date": row["due_date"],
                        "amount": row["amount"],
                        "control_account_id": accounts[row["control_account_code"]].id,
                    }
                )
                openings.validate(
                    db,
                    bid,
                    opening_row,
                    data.cutover_date,
                    equity.id,
                    accounts=account_ids,
                    parties=parties,
                    business=business,
                )
                opening_ref = (opening_row.kind, opening_row.party_id, opening_row.reference)
                if opening_ref in opening_refs:
                    raise ValueError("Repeated opening source")
                opening_refs.add(opening_ref)
                commands.append(
                    {
                        "row": position,
                        "action": "opening",
                        "payload": {
                            **opening_row.model_dump(mode="json"),
                            "cutover_date": data.cutover_date.isoformat(),
                            "equity_account_id": str(equity.id),
                            "review_reason": data.review_reason,
                        },
                    }
                )
                continue
            if data.mode == "chart":
                chart = AccountCreate.model_validate(
                    {
                        "code": row["code"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "control": row.get("control") or None,
                    }
                )
                if chart.code in accounts or chart.code in seen_codes:
                    raise ValueError("Account code already exists")
                seen_codes.add(chart.code)
                commands.append(
                    {"row": position, "action": "account", "payload": chart.model_dump()}
                )
                continue
            day = date.fromisoformat(row["accounting_date"])
            if day > data.cutover_date:
                raise ValueError("Source date is after cutover")
            if data.mode == "journal":
                account = accounts[row["account_code"]]
                line = LineCreate(
                    account_id=account.id,
                    debit=row.get("debit") or "0",
                    credit=row.get("credit") or "0",
                )
                ops.money(line.debit, business, positive=False)
                ops.money(line.credit, business, positive=False)
                reference = row["reference"]
                item = grouped.setdefault(
                    reference,
                    {
                        "row": position,
                        "action": "journal",
                        "payload": {
                            "accounting_date": day.isoformat(),
                            "memo": row.get("memo", ""),
                            "lines": [],
                        },
                    },
                )
                if item["payload"]["accounting_date"] != day.isoformat():
                    raise ValueError("One journal reference cannot have different dates")
                item["payload"]["lines"].append(line.model_dump(mode="json"))
            else:
                kind = row["kind"]
                party_id = UUID(row["party_id"])
                payload: dict[str, Any] = {
                    "kind": kind,
                    "party_id": str(party_id),
                    "accounting_date": day.isoformat(),
                    "control_account_id": str(accounts[row["control_account_code"]].id),
                }
                if kind in {"invoice", "bill", "sales_receipt"}:
                    payload.update(
                        service_start=row.get("service_start") or None,
                        service_end=row.get("service_end") or None,
                        number=row["number"],
                        due_date=row["due_date"],
                        lines=[
                            {
                                "account_id": str(accounts[row["category_account_code"]].id),
                                "description": row.get("description") or "Imported dated source",
                                "amount": row["amount"],
                                "tax_amount": row.get("tax_amount") or "0",
                                "tax_account_id": str(accounts[row["tax_account_code"]].id)
                                if row.get("tax_account_code")
                                else None,
                                "tax_code": row.get("tax_code") or None,
                            }
                        ],
                    )
                    if kind == "sales_receipt":
                        payload["bank_account_id"] = str(accounts[row["bank_account_code"]].id)
                    source = DocumentCreate.model_validate(
                        {**payload, "expected_version": 0, "idempotency_key": "validation-only-key"}
                    )
                    document_impact(db, business, source, accounts=account_ids, parties=parties)
                    commands.append({"row": position, "action": "document", "payload": payload})
                elif kind in {"receipt", "payment"}:
                    payload.update(
                        bank_account_id=str(accounts[row["bank_account_code"]].id),
                        amount=row["amount"],
                        allocations=[],
                    )
                    PaymentCreate.model_validate(
                        {**payload, "expected_version": 0, "idempotency_key": "validation-only-key"}
                    )
                    commands.append(
                        {
                            "row": position,
                            "action": "payment",
                            "payload": payload,
                            "document_number": row["document_number"],
                        }
                    )
                elif kind in {"loan_drawdown", "loan_repayment"}:
                    from ledgerpilot.shared.middleware import excessive_depth

                    schedule = row.get("schedule_json") or "[]"
                    if len(schedule) > 10000 or excessive_depth(schedule.encode()):
                        raise ValueError("Loan schedule JSON exceeds limits")
                    loan = LoanCreate.model_validate(
                        {
                            "party_id": party_id,
                            "direction": row["direction"],
                            "control_account_id": accounts[row["control_account_code"]].id,
                            "name": row["loan_name"],
                            "schedule": json.loads(schedule),
                        }
                    )
                    movement = LoanMovementCreate.model_validate(
                        {
                            "kind": "drawdown" if kind == "loan_drawdown" else "repayment",
                            "accounting_date": day.isoformat(),
                            "bank_account_id": str(accounts[row["bank_account_code"]].id),
                            "principal": row["amount"],
                            "interest": row.get("interest") or "0",
                            "fees": row.get("fees") or "0",
                            "interest_account_id": str(accounts[row["category_account_code"]].id)
                            if row.get("category_account_code")
                            else None,
                            "fee_account_id": str(accounts[row["fee_account_code"]].id)
                            if row.get("fee_account_code")
                            else None,
                            "expected_version": 0,
                            "idempotency_key": "validation-only-key",
                        }
                    )
                    commands.append(
                        {
                            "row": position,
                            "action": "loan",
                            "payload": {
                                "loan": loan.model_dump(mode="json"),
                                "movement": movement.model_dump(mode="json"),
                            },
                        }
                    )
                else:
                    raise ValueError(
                        "Unsupported source kind; use the explicit credit/refund/advance workflows"
                    )
        except (KeyError, ValueError, ValidationError, DomainError):
            errors.append(
                {
                    "row": position,
                    "code": "ERR_IMPORT_ROW",
                    "message": (
                        "Check mappings, codes, source dates, party IDs and exact values; "
                        "unsupported sources require manual review."
                    ),
                }
            )
    if data.mode == "journal":
        for item in grouped.values():
            values = item["payload"]["lines"]
            try:
                ImportJournal.model_validate(
                    {
                        **item["payload"],
                        "idempotency_key": "preview-journal-key",
                        "expected_version": 0,
                    }
                )
            except ValidationError:
                errors.append(
                    {
                        "row": item["row"],
                        "code": "ERR_IMPORT_ROW",
                        "message": "Journal fields do not meet the posting contract.",
                    }
                )
            if (
                len(values) < 2
                or len(values) > 200
                or sum((Decimal(v["debit"]) - Decimal(v["credit"]) for v in values), Decimal(0))
            ):
                errors.append(
                    {
                        "row": item["row"],
                        "code": "ERR_BALANCE",
                        "message": "Every journal group must have two to 200 balanced lines.",
                    }
                )
            commands.append(item)
    if data.mode != "chart" and len(commands) > 25:
        raise DomainError(
            "ERR_QUOTA", "Pilot financial batches support at most 25 operations.", 413
        )
    if data.mode != "chart" and not data.expected_trial:
        errors.append(
            {
                "row": 0,
                "code": "ERR_BASELINE_REQUIRED",
                "message": "Supply the expected legacy trial balance for financial imports.",
            }
        )
    for code, value in data.expected_trial.items():
        try:
            if code not in accounts:
                raise ValueError("Unknown expected account")
            if not re.fullmatch(r"-?[0-9]{1,18}(?:\.[0-9]{1,6})?", value):
                raise ValueError("Use a signed exact decimal")
            amount = Decimal(value)
            ops.money(format(abs(amount), "f"), business, positive=False)
        except (ValueError, DomainError):
            errors.append(
                {
                    "row": 0,
                    "code": "ERR_EXPECTED_TOTAL",
                    "message": (
                        "Expected trial totals require known codes and exact signed balances."
                    ),
                }
            )
    return commands, errors
