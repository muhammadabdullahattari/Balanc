"""Reviewed, bounded CSV imports compile to the same public accounting workflows.

No missing source dates, counterparties, categories or historical allocations are
invented. GL control accounts are accepted only when final subledgers tie out.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Account
from ledgerpilot.accounting.schemas import AccountCreate
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.reports.service import balances, controls
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import Loan, TradeDocument
from ledgerpilot.trade.schemas import DocumentCreate, LoanCreate, LoanMovementCreate, PaymentCreate
from ledgerpilot.trade.service import (
    create_document,
    create_loan,
    create_payment,
    loan_movement,
)
from ledgerpilot.workspace import openings
from ledgerpilot.workspace.importing.compiler import compile_rows
from ledgerpilot.workspace.importing.schemas import ImportApply, ImportJournal, ImportPreview
from ledgerpilot.workspace.models import ImportBatch


def preview(db: Session, who: Principal, bid: UUID, data: ImportPreview) -> dict[str, Any]:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    commands, errors = compile_rows(db, bid, data)
    digest = hashlib.sha256(
        json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "preview_hash": digest,
        "commands": commands,
        "errors": errors,
        "can_apply": not errors,
        "posted": False,
        "cutover_date": data.cutover_date,
        "business_version": get_business(db, bid).version,
        "required_approval_role": "accountant" if data.mode == "opening" else None,
        "policy": (
            "Opening balances require accountant approval and explicit equity; "
            "other financial imports require complete dated sources."
        ),
    }


def apply(db: Session, who: Principal, bid: UUID, data: ImportApply) -> dict[str, Any]:
    if data.mode == "opening":
        authorize(db, who, bid, {"accountant"}, posting=True)
    business, old = ops.begin(db, who, bid, data, "migration.applied")
    if old is not None:
        return old
    root_operation = db.info["operation_id"]
    file_hash = hashlib.sha256(data.content.encode()).hexdigest()
    if db.scalar(
        select(ImportBatch.id).where(
            ImportBatch.business_id == bid,
            ImportBatch.mode == data.mode,
            ImportBatch.file_hash == file_hash,
        )
    ):
        raise DomainError(
            "ERR_FILE_ALREADY_IMPORTED",
            "This file is already in import history; review its existing result.",
            409,
        )
    request = ImportPreview.model_validate(
        data.model_dump(exclude={"idempotency_key", "expected_version", "preview_hash"})
    )
    planned = preview(db, who, bid, request)
    if planned["preview_hash"] != data.preview_hash or planned["errors"]:
        raise DomainError(
            "ERR_IMPORT_PREVIEW", "Review a valid current preview before applying.", 409
        )
    results = []
    for index, command in enumerate(planned["commands"]):
        key = "migration." + hashlib.sha256(f"{data.idempotency_key}:{index}".encode()).hexdigest()
        payload = command["payload"]
        if command["action"] == "account":
            account = Account(business_id=bid, **AccountCreate.model_validate(payload).model_dump())
            db.add(account)
            db.flush()
            record(db, who.user.id, "account.imported", bid, account.id)
            results.append({"row": command["row"], "id": str(account.id)})
            continue
        db.refresh(business)
        common = {"idempotency_key": key, "expected_version": business.version}
        if command["action"] == "opening":
            result = openings.create(
                db, who, bid, openings.OpeningCommand.model_validate({**payload, **common})
            )
        elif command["action"] == "document":
            result = create_document(
                db, who, bid, DocumentCreate.model_validate({**payload, **common})
            )
        elif command["action"] == "payment":
            doc = db.scalar(
                select(TradeDocument).where(
                    TradeDocument.business_id == bid,
                    TradeDocument.party_id == UUID(payload["party_id"]),
                    TradeDocument.number == command["document_number"],
                    TradeDocument.kind == ("invoice" if payload["kind"] == "receipt" else "bill"),
                )
            )
            if doc is None:
                raise DomainError(
                    "ERR_IMPORT_SOURCE",
                    "Import the complete original document before its allocation.",
                )
            payload = {
                **payload,
                "allocations": [{"document_id": str(doc.id), "amount": payload["amount"]}],
            }
            result = create_payment(
                db, who, bid, PaymentCreate.model_validate({**payload, **common})
            )
        elif command["action"] == "loan":
            source = LoanCreate.model_validate(payload["loan"])
            movement = LoanMovementCreate.model_validate({**payload["movement"], **common})
            candidates = list(
                db.scalars(
                    select(Loan)
                    .where(
                        Loan.business_id == bid,
                        Loan.party_id == source.party_id,
                        Loan.name == source.name,
                        Loan.control_account_id == source.control_account_id,
                        Loan.direction == source.direction,
                    )
                    .limit(2)
                )
            )
            if len(candidates) > 1 or (not candidates and movement.kind == "repayment"):
                raise DomainError(
                    "ERR_IMPORT_SOURCE", "Loan reference is missing or ambiguous.", 409
                )
            loan = candidates[0] if candidates else create_loan(db, who, bid, source)
            if source.schedule and loan.schedule != [
                s.model_dump(mode="json") for s in source.schedule
            ]:
                raise DomainError(
                    "ERR_IMPORT_SOURCE",
                    "Existing loan schedules cannot be replaced during import.",
                    409,
                )
            result = loan_movement(db, who, bid, loan.id, movement)
        else:
            child = ImportJournal.model_validate({**payload, **common})
            child_business, existing = ops.begin(db, who, bid, child, "migration.journal")
            if existing:
                result = existing
            else:
                jid = ops.publish(
                    db, who, child_business, child, child.accounting_date, child.memo, child.lines
                )
                result = ops.finish(
                    db,
                    who,
                    bid,
                    child,
                    "migration.journal",
                    {"id": str(jid), "journal_id": str(jid)},
                    jid,
                )
        results.append({"row": command["row"], **result})
    db.flush()
    if data.mode != "chart":
        tieout = controls(db, bid, data.cutover_date)
        if not tieout["balanced"]:
            raise DomainError(
                "ERR_IMPORT_CONTROL",
                "Imported controls must reconcile; all batch changes were rolled back.",
                409,
            )
        if data.expected_trial:
            actual = balances(db, bid, data.cutover_date)
            accounts = list(db.scalars(select(Account).where(Account.business_id == bid)))
            if any(
                actual.get(a.id, Decimal(0)) != Decimal(data.expected_trial.get(a.code, "0"))
                for a in accounts
            ):
                raise DomainError(
                    "ERR_IMPORT_TOTAL",
                    "Trial balance differs from the supplied legacy baseline; batch rolled back.",
                    409,
                )
    item = ImportBatch(
        business_id=bid,
        file_hash=file_hash,
        mode=data.mode,
        cutover_date=data.cutover_date,
        source=data.content,
        results=results,
        created_by=who.user.id,
        expected_trial=data.expected_trial,
    )
    db.add(item)
    db.flush()
    db.info["operation_id"] = root_operation
    return ops.finish(
        db,
        who,
        bid,
        data,
        "migration.applied",
        {"id": str(item.id), "applied": len(results), "results": results, "atomic": True},
    )
