"""Bank review and suggestions are explicit tenant-authorized actions."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from ledgerpilot.audit.service import record
from ledgerpilot.banking import corrections, service
from ledgerpilot.banking.models import BankCorrection, BankImport, BankRow, BankRule
from ledgerpilot.banking.read import BankImportRead, BankImportResult, BankRowRead, DispositionRead
from ledgerpilot.banking.schemas import (
    BankDisposition,
    CSVImport,
    CSVPreview,
    RuleActivation,
    RuleCreate,
    RuleRollback,
    RuleUpdate,
    StatementCreate,
)
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.operations import account
from ledgerpilot.shared.responses import Page
from ledgerpilot.tenancy.service import authorize, get_business

router = APIRouter(prefix="/businesses/{business_id}", tags=["banking"])


@router.get(
    "/bank-rules/correction-suggestions",
    response_model=contracts.responses["banking.correction_suggestions"],
    response_model_exclude_unset=True,
)
def correction_suggestions(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    from ledgerpilot.banking.suggestions import correction_rules

    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
    return correction_rules(db, business_id)


@router.post(
    "/bank-transactions/{row_id}/reclassifications",
    status_code=201,
    response_model=contracts.responses["banking.reclassify"],
    response_model_exclude_unset=True,
)
def reclassify(
    business_id: UUID,
    row_id: UUID,
    data: corrections.Reclassify,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return corrections.correct(db, identity, business_id, row_id, data)


@router.get(
    "/bank-transactions/{row_id}/reclassifications",
    response_model=contracts.responses["banking.reclassifications"],
    response_model_exclude_unset=True,
)
def reclassifications(
    business_id: UUID,
    row_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    service.bank_row(db, business_id, row_id)
    query = select(BankCorrection).where(
        BankCorrection.business_id == business_id, BankCorrection.row_id == row_id
    )
    if cursor:
        query = query.where(BankCorrection.id > cursor)
    rows = list(db.scalars(query.order_by(BankCorrection.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "row_id",
                    "accounting_date",
                    "journal_id",
                    "reason",
                    "splits",
                    "created_by",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post(
    "/bank-transactions/{row_id}/preview",
    response_model=contracts.responses["banking.disposition_preview"],
    response_model_exclude_unset=True,
)
def disposition_preview(
    business_id: UUID,
    row_id: UUID,
    data: BankDisposition,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return service.dispose(db, identity, business_id, row_id, data, preview_only=True)


@router.get(
    "/bank-transactions/{row_id}/matches",
    response_model=contracts.responses["banking.matches"],
    response_model_exclude_unset=True,
)
def matches(business_id: UUID, row_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    from ledgerpilot.accounting.models import Journal
    from ledgerpilot.banking.models import BankMatch

    authorize(db, identity, business_id)
    service.bank_row(db, business_id, row_id)
    rows = db.execute(
        select(
            Journal.id,
            Journal.accounting_date,
            Journal.memo,
            Journal.source_kind,
            Journal.source_id,
        )
        .join(
            BankMatch,
            (BankMatch.journal_id == Journal.id) & (BankMatch.business_id == Journal.business_id),
        )
        .where(BankMatch.business_id == business_id, BankMatch.row_id == row_id)
        .order_by(Journal.accounting_date, Journal.id)
        .limit(20)
    )
    return {"items": [dict(r) for r in rows.mappings()]}


@router.post(
    "/bank-imports/preview",
    response_model=contracts.responses["banking.preview"],
    response_model_exclude_unset=True,
)
def preview(business_id: UUID, data: CSVPreview, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    account(db, business_id, data.account_id, {"asset", "liability"}, plain=True)
    result = service.preview(data, get_business(db, business_id))
    service.import_limit(db, business_id, result)
    return result


@router.post("/bank-imports", status_code=201, response_model=BankImportResult)
def import_csv(business_id: UUID, data: CSVImport, identity: Identity, db: DB) -> dict[str, Any]:
    return service.import_csv(db, identity, business_id, data)


@router.get("/bank-imports", response_model=Page[BankImportRead])
def imports(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(BankImport).where(BankImport.business_id == business_id)
    if cursor:
        query = query.where(BankImport.id > cursor)
    rows = list(db.scalars(query.order_by(BankImport.id).limit(limit + 1)))
    return {
        "items": [
            {
                k: str(getattr(r, k)) if k in {"total", "amount"} else getattr(r, k)
                for k in (
                    "id",
                    "account_id",
                    "file_hash",
                    "mapping",
                    "row_count",
                    "created_at",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get("/bank-transactions", response_model=Page[BankRowRead])
def transactions(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    state: Literal["for_review", "categorized", "matched", "excluded"] = "for_review",
    account_id: UUID | None = None,
    q: str = Query("", max_length=100),
    start: date | None = None,
    end: date | None = None,
    min_amount: Annotated[
        Decimal | None, Query(ge=-999999999999999999, le=999999999999999999)
    ] = None,
    max_amount: Annotated[
        Decimal | None, Query(ge=-999999999999999999, le=999999999999999999)
    ] = None,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    if (start and end and start > end) or (
        min_amount is not None and max_amount is not None and min_amount > max_amount
    ):
        raise DomainError("ERR_FILTER", "Filter ranges must be ordered.", 422)
    query = select(BankRow).where(BankRow.business_id == business_id, BankRow.state == state)
    if cursor:
        query = query.where(BankRow.id > cursor)
    if account_id:
        query = query.where(BankRow.account_id == account_id)
    if q:
        query = query.where(BankRow.description.icontains(q, autoescape=True))
    if start:
        query = query.where(BankRow.accounting_date >= start)
    if end:
        query = query.where(BankRow.accounting_date <= end)
    if min_amount is not None:
        query = query.where(BankRow.amount >= min_amount)
    if max_amount is not None:
        query = query.where(BankRow.amount <= max_amount)
    rows = list(db.scalars(query.order_by(BankRow.id).limit(limit + 1)))
    reverse_peers = {
        peer_id: row_id
        for row_id, peer_id in db.execute(
            select(BankRow.id, BankRow.peer_id).where(
                BankRow.business_id == business_id,
                BankRow.peer_id.in_([r.id for r in rows[:limit]]),
            )
        )
    }
    return {
        "items": [
            {
                k: (r.peer_id or reverse_peers.get(r.id))
                if k == "peer_id"
                else str(getattr(r, k))
                if k in {"total", "amount"}
                else getattr(r, k)
                for k in (
                    "id",
                    "account_id",
                    "import_id",
                    "accounting_date",
                    "description",
                    "amount",
                    "state",
                    "version",
                    "journal_id",
                    "reason",
                    "peer_id",
                    "external_id",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/bank-transactions/{row_id}/disposition", response_model=DispositionRead)
def disposition(
    business_id: UUID,
    row_id: UUID,
    data: BankDisposition,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return service.dispose(db, identity, business_id, row_id, data)


@router.post(
    "/bank-rules",
    status_code=201,
    response_model=contracts.responses["banking.rule_create"],
    response_model_exclude_unset=True,
)
def rule_create(business_id: UUID, data: RuleCreate, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    account(db, business_id, data.account_id, {"income", "expense"}, plain=True)
    from sqlalchemy import func

    get_business(db, business_id, lock=True)
    count = (
        db.scalar(
            select(func.count())
            .select_from(BankRule)
            .where(
                BankRule.business_id == business_id,
            )
        )
        or 0
    )
    if count >= 200:
        raise DomainError("ERR_QUOTA", "The pilot supports at most 200 rules.", 413)
    item = BankRule(business_id=business_id, **data.model_dump())
    db.add(item)
    db.flush()
    record(db, identity.user.id, "rule.created", business_id, item.id)
    return {"id": str(item.id), "version": item.version, "active": item.active}


def rule(db: DB, bid: UUID, rid: UUID) -> BankRule:
    item = db.scalar(select(BankRule).where(BankRule.business_id == bid, BankRule.id == rid))
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Rule not found.", 404)
    return item


@router.get(
    "/bank-rules",
    response_model=contracts.responses["banking.rules"],
    response_model_exclude_unset=True,
)
def rules(business_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    rows = list(
        db.scalars(
            select(BankRule)
            .where(BankRule.business_id == business_id)
            .order_by(BankRule.priority, BankRule.id)
            .limit(201)
        )
    )
    if len(rows) > 200:
        raise DomainError("ERR_QUOTA", "The pilot supports at most 200 categorization rules.", 413)
    return {
        "items": [
            {
                k: str(getattr(r, k)) if k in {"total", "amount"} else getattr(r, k)
                for k in (
                    "id",
                    "name",
                    "contains",
                    "account_id",
                    "priority",
                    "version",
                    "active",
                    "history",
                )
            }
            for r in rows
        ]
    }


@router.get(
    "/bank-rules/{rule_id}/preview",
    response_model=contracts.responses["banking.preview_rule"],
    response_model_exclude_unset=True,
)
def preview_rule(business_id: UUID, rule_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    return service.rule_preview(db, business_id, rule(db, business_id, rule_id))


def snapshot(item: BankRule) -> dict[str, Any]:
    return {
        k: str(getattr(item, k)) if k == "account_id" else getattr(item, k)
        for k in (
            "name",
            "contains",
            "account_id",
            "priority",
            "version",
            "active",
        )
    }


@router.patch(
    "/bank-rules/{rule_id}",
    response_model=contracts.responses["banking.update_rule"],
    response_model_exclude_unset=True,
)
def update_rule(
    business_id: UUID,
    rule_id: UUID,
    data: RuleUpdate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    item = rule(db, business_id, rule_id)
    if item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Rule changed.", 409)
    account(db, business_id, data.account_id, {"income", "expense"}, plain=True)
    item.history = [*item.history, snapshot(item)]
    for key, value in data.model_dump(exclude={"expected_version"}).items():
        setattr(item, key, value)
    item.active, item.preview_hash = False, None
    item.version += 1
    record(db, identity.user.id, "rule.updated", business_id, item.id)
    return snapshot(item)


@router.post(
    "/bank-rules/{rule_id}/activation",
    response_model=contracts.responses["banking.activate_rule"],
    response_model_exclude_unset=True,
)
def activate_rule(
    business_id: UUID,
    rule_id: UUID,
    data: RuleActivation,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    item = rule(db, business_id, rule_id)
    if item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Rule changed.", 409)
    if (
        data.active
        and data.preview_hash != service.rule_preview(db, business_id, item)["preview_hash"]
    ):
        raise DomainError("ERR_RULE_PREVIEW", "Review a fresh preview before activating.", 409)
    item.history = [*item.history, snapshot(item)]
    item.active, item.preview_hash = data.active, data.preview_hash
    item.version += 1
    record(
        db,
        identity.user.id,
        "rule.activated" if data.active else "rule.paused",
        business_id,
        item.id,
    )
    return snapshot(item)


@router.get(
    "/bank-transactions/{row_id}/proposal",
    response_model=contracts.responses["banking.proposal"],
    response_model_exclude_unset=True,
)
def proposal(business_id: UUID, row_id: UUID, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    row = service.bank_row(db, business_id, row_id)
    candidates = list(
        db.scalars(
            select(BankRule)
            .where(
                BankRule.business_id == business_id,
                BankRule.active.is_(True),
            )
            .order_by(BankRule.priority, BankRule.id)
            .limit(201)
        )
    )
    matches = [r for r in candidates if r.contains.casefold() in row.description.casefold()]
    return {
        "row_id": str(row_id),
        "mode": "deterministic_tenant_rules",
        "model_version": None,
        "confidence": None,
        "requires_approval": True,
        "status": "proposed" if matches else "abstained",
        "candidates": [
            {
                "rule_id": str(r.id),
                "rule_version": r.version,
                "account_id": str(r.account_id),
                "reason": "Approved description rule matched.",
            }
            for r in matches
        ],
        "posted": False,
    }


@router.post(
    "/bank-rules/{rule_id}/rollback",
    response_model=contracts.responses["banking.rollback_rule"],
    response_model_exclude_unset=True,
)
def rollback_rule(
    business_id: UUID,
    rule_id: UUID,
    data: RuleRollback,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    item = rule(db, business_id, rule_id)
    if item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Rule changed.", 409)
    old = next((h for h in item.history if h["version"] == data.restore_version), None)
    if old is None:
        raise DomainError("ERR_NOT_FOUND", "Historical rule version not found.", 404)
    return update_rule(
        business_id,
        rule_id,
        RuleUpdate(
            name=old["name"],
            contains=old["contains"],
            account_id=UUID(old["account_id"]),
            priority=old["priority"],
            expected_version=data.expected_version,
        ),
        identity,
        db,
    )


@router.post(
    "/statements/sign-off",
    status_code=201,
    response_model=contracts.responses["banking.sign_off"],
    response_model_exclude_unset=True,
)
def sign_off(
    business_id: UUID,
    data: StatementCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    from ledgerpilot.banking.reconciliation import sign_off as reconcile

    return reconcile(db, identity, business_id, data)
