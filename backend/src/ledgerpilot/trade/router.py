"""Internal party, sales, purchasing and loan APIs."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.responses import Page
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade import advances, service
from ledgerpilot.trade import drafts as source_drafts
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.models import (
    Loan,
    LoanMovement,
    Operation,
    Party,
    Payment,
    SourceDraft,
    TradeDocument,
)
from ledgerpilot.trade.read import (
    DocumentPage,
    DocumentRead,
    IssuedRead,
    LoanRead,
    PartyRead,
    PaymentRead,
    PostedRead,
)
from ledgerpilot.trade.schemas import (
    DisputeUpdate,
    DocumentCreate,
    LoanCreate,
    LoanMovementCreate,
    PartyCreate,
    PartyUpdate,
    PaymentCreate,
)
from ledgerpilot.workspace.models import Advance, AdvanceApplication

router = APIRouter(prefix="/businesses/{business_id}", tags=["trade"])


@router.get(
    "/parties/{party_id}/statement",
    response_model=contracts.responses["trade.party_statement"],
    response_model_exclude_unset=True,
)
def party_statement(
    business_id: UUID,
    party_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    role: Literal["customer", "supplier", "lender", "borrower"] = "customer",
    cursor: str | None = Query(default=None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    from ledgerpilot.trade.statements import statement

    authorize(db, identity, business_id)
    return statement(db, identity, business_id, party_id, as_of, role, limit, cursor)


@router.post(
    "/trade-documents/preview",
    response_model=contracts.responses["trade.preview_document"],
    response_model_exclude_unset=True,
)
def preview_document(
    business_id: UUID,
    data: DocumentCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    business = get_business(db, business_id)
    lines, total, tax = service.document_impact(db, business, data)
    return {
        "currency": business.currency,
        "timezone": business.timezone,
        "business_version": business.version,
        "total": str(total),
        "tax": str(tax),
        "lines": [line.model_dump(mode="json") for line in lines],
        "posted": False,
    }


@router.post(
    "/source-drafts",
    status_code=201,
    response_model=contracts.responses["trade.source_draft_create"],
    response_model_exclude_unset=True,
)
def source_draft_create(
    business_id: UUID,
    data: DocumentCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return source_drafts.create(db, identity, business_id, data)


@router.patch(
    "/source-drafts/{draft_id}",
    response_model=contracts.responses["trade.source_draft_edit"],
    response_model_exclude_unset=True,
)
def source_draft_edit(
    business_id: UUID,
    draft_id: UUID,
    data: source_drafts.DraftEdit,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return source_drafts.update(db, identity, business_id, draft_id, data)


@router.post(
    "/source-drafts/{draft_id}/issue",
    response_model=contracts.responses["trade.source_draft_issue"],
    response_model_exclude_unset=True,
)
def source_draft_issue(
    business_id: UUID,
    draft_id: UUID,
    data: source_drafts.DraftAction,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return source_drafts.publish(db, identity, business_id, draft_id, data)


@router.post(
    "/source-drafts/{draft_id}/void",
    response_model=contracts.responses["trade.source_draft_void"],
    response_model_exclude_unset=True,
)
def source_draft_void(
    business_id: UUID,
    draft_id: UUID,
    data: source_drafts.DraftAction,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return source_drafts.void(db, identity, business_id, draft_id, data)


@router.get(
    "/source-drafts",
    response_model=contracts.responses["trade.source_draft_list"],
    response_model_exclude_unset=True,
)
def source_draft_list(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(SourceDraft).where(SourceDraft.business_id == business_id)
    if cursor:
        query = query.where(SourceDraft.id > cursor)
    rows = list(db.scalars(query.order_by(SourceDraft.id).limit(limit + 1)))
    return {
        "items": [source_drafts.read(r) for r in rows[:limit]],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/operations/{operation_id}",
    response_model=contracts.responses["trade.operation"],
    response_model_exclude_unset=True,
)
def operation(
    business_id: UUID,
    operation_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    item = db.scalar(
        select(Operation).where(Operation.business_id == business_id, Operation.id == operation_id)
    )
    if item is None:
        raise DomainError("ERR_NOT_FOUND", "Operation not found.", 404)
    return {
        "id": str(item.id),
        "action": item.action,
        "journal_id": item.journal_id,
        "result": item.result,
        "created_at": item.created_at,
    }


@router.get(
    "/advances",
    response_model=contracts.responses["trade.advances_list"],
    response_model_exclude_unset=True,
)
def advances_list(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    from sqlalchemy import func

    authorize(db, identity, business_id)
    query = select(Advance).where(
        Advance.business_id == business_id, Advance.accounting_date <= as_of
    )
    if cursor:
        query = query.where(Advance.id > cursor)
    rows = list(db.scalars(query.order_by(Advance.id).limit(limit + 1)))
    used = dict(
        db.execute(
            select(AdvanceApplication.advance_id, func.sum(AdvanceApplication.amount))
            .where(
                AdvanceApplication.business_id == business_id,
                AdvanceApplication.advance_id.in_([r.id for r in rows[:limit]]),
                AdvanceApplication.accounting_date <= as_of,
            )
            .group_by(AdvanceApplication.advance_id)
        ).all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "party_id": str(r.party_id),
                "direction": r.direction,
                "accounting_date": r.accounting_date,
                "amount": str(r.amount),
                "unapplied": str(r.amount - used.get(r.id, Decimal(0))),
                "deposit_account_id": str(r.deposit_account_id),
                "journal_id": str(r.journal_id),
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.get(
    "/loans/{loan_id}/movements",
    response_model=contracts.responses["trade.movements"],
    response_model_exclude_unset=True,
)
def movements(
    business_id: UUID,
    loan_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    repo.loan(db, business_id, loan_id)
    query = select(LoanMovement).where(
        LoanMovement.business_id == business_id, LoanMovement.loan_id == loan_id
    )
    if cursor:
        query = query.where(LoanMovement.id > cursor)
    rows = list(db.scalars(query.order_by(LoanMovement.id).limit(limit + 1)))
    return {
        "items": [
            {
                "id": str(r.id),
                "accounting_date": r.accounting_date,
                "kind": r.kind,
                "principal": str(r.principal),
                "interest": str(r.interest),
                "fees": str(r.fees),
                "journal_id": str(r.journal_id),
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post(
    "/advances",
    status_code=201,
    response_model=contracts.responses["trade.advance_create"],
    response_model_exclude_unset=True,
)
def advance_create(
    business_id: UUID,
    data: advances.AdvanceCreate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return advances.create(db, identity, business_id, data)


@router.post(
    "/advances/{advance_id}/applications",
    status_code=201,
    response_model=contracts.responses["trade.advance_apply"],
    response_model_exclude_unset=True,
)
def advance_apply(
    business_id: UUID,
    advance_id: UUID,
    data: advances.AdvanceApply,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return advances.apply(db, identity, business_id, advance_id, data)


def party_read(item: Party) -> dict[str, Any]:
    return {
        key: getattr(item, key) for key in ("id", "name", "email", "roles", "active", "version")
    }


def document_read(item: TradeDocument, due: Decimal | None = None) -> dict[str, Any]:
    result = {
        **{
            key: getattr(item, key)
            for key in (
                "id",
                "kind",
                "number",
                "party_id",
                "accounting_date",
                "opening_source_date",
                "due_date",
                "service_start",
                "service_end",
                "control_account_id",
                "journal_id",
                "total",
                "tax",
                "lines",
                "original_id",
                "disputed",
                "version",
            )
        },
        "outstanding": str(due) if due is not None else None,
    }
    result["total"], result["tax"] = str(item.total), str(item.tax)
    return result


@router.post("/parties", status_code=201, response_model=PartyRead)
def create_party(
    business_id: UUID, data: PartyCreate, identity: Identity, db: DB
) -> dict[str, Any]:
    return party_read(service.create_party(db, identity, business_id, data))


@router.patch("/parties/{party_id}", response_model=PartyRead)
def update_party(
    business_id: UUID,
    party_id: UUID,
    data: PartyUpdate,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    return party_read(service.update_party(db, identity, business_id, party_id, data))


@router.get("/parties", response_model=Page[PartyRead])
def parties(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    q: str = Query("", max_length=100),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = (
        select(Party).where(Party.business_id == business_id).order_by(Party.id).limit(limit + 1)
    )
    if cursor:
        query = query.where(Party.id > cursor)
    if q:
        query = query.where(Party.name.icontains(q, autoescape=True))
    rows = list(db.scalars(query))
    return {
        "items": [party_read(p) for p in rows[:limit]],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/trade-documents", status_code=201, response_model=IssuedRead)
def issue(business_id: UUID, data: DocumentCreate, identity: Identity, db: DB) -> dict[str, object]:
    return service.create_document(db, identity, business_id, data)


@router.get("/trade-documents", response_model=DocumentPage)
def documents(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    kind: str | None = None,
    party_id: UUID | None = None,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    business = get_business(db, business_id)
    query = select(TradeDocument).where(
        TradeDocument.business_id == business_id, TradeDocument.accounting_date <= as_of
    )
    if kind:
        query = query.where(TradeDocument.kind == kind)
    if party_id:
        query = query.where(TradeDocument.party_id == party_id)
    if cursor:
        query = query.where(TradeDocument.id > cursor)
    rows = list(db.scalars(query.order_by(TradeDocument.id).limit(limit + 1)))
    balances = repo.due(db, business_id, as_of)
    return {
        "items": [document_read(d, balances.get(d.id)) for d in rows[:limit]],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        "as_of": as_of,
        "currency": business.currency,
        "timezone": business.timezone,
        "basis": "operational_accrual",
    }


@router.get("/trade-documents/{document_id}", response_model=DocumentRead)
def document(
    business_id: UUID,
    document_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    item = repo.document(db, business_id, document_id)
    return document_read(item, repo.due(db, business_id, as_of).get(document_id))


@router.patch(
    "/trade-documents/{document_id}/dispute",
    response_model=contracts.responses["trade.dispute"],
    response_model_exclude_unset=True,
)
def dispute(
    business_id: UUID,
    document_id: UUID,
    data: DisputeUpdate,
    identity: Identity,
    db: DB,
) -> dict[str, object]:
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    get_business(db, business_id, lock=True)
    item = repo.document(db, business_id, document_id)
    if item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Document changed.", 409)
    item.disputed = data.disputed
    item.version += 1
    record(
        db,
        identity.user.id,
        "trade.disputed",
        business_id,
        document_id,
        {"reason": data.reason, "disputed": data.disputed},
    )
    return {"id": str(item.id), "version": item.version}


@router.post("/payments", status_code=201, response_model=PostedRead)
def payment(
    business_id: UUID, data: PaymentCreate, identity: Identity, db: DB
) -> dict[str, object]:
    return service.create_payment(db, identity, business_id, data)


@router.get("/payments", response_model=Page[PaymentRead])
def payments(
    business_id: UUID,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    party_id: UUID | None = None,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(Payment).where(Payment.business_id == business_id)
    if party_id:
        query = query.where(Payment.party_id == party_id)
    if cursor:
        query = query.where(Payment.id > cursor)
    rows = list(db.scalars(query.order_by(Payment.id).limit(limit + 1)))
    return {
        "items": [
            {
                key: str(getattr(r, key))
                if key in {"total", "amount", "fee_amount"}
                else getattr(r, key)
                for key in (
                    "id",
                    "kind",
                    "party_id",
                    "accounting_date",
                    "bank_account_id",
                    "control_account_id",
                    "total",
                    "fee_amount",
                    "fee_account_id",
                    "journal_id",
                    "allocations",
                    "original_payment_id",
                )
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post(
    "/loans",
    status_code=201,
    response_model=contracts.responses["trade.loan"],
    response_model_exclude_unset=True,
)
def loan(business_id: UUID, data: LoanCreate, identity: Identity, db: DB) -> dict[str, object]:
    return {"id": str(service.create_loan(db, identity, business_id, data).id)}


@router.get("/loans", response_model=Page[LoanRead])
def loans(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    query = select(Loan).where(Loan.business_id == business_id)
    if cursor:
        query = query.where(Loan.id > cursor)
    rows = list(db.scalars(query.order_by(Loan.id).limit(limit + 1)))
    principals = repo.principal_balances(db, business_id, [r.id for r in rows[:limit]], as_of)
    return {
        "items": [
            {
                **{
                    k: str(getattr(r, k)) if k in {"total", "amount"} else getattr(r, k)
                    for k in (
                        "id",
                        "name",
                        "party_id",
                        "direction",
                        "control_account_id",
                        "schedule",
                    )
                },
                "principal": str(principals.get(r.id, Decimal(0))),
            }
            for r in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/loans/{loan_id}/movements", status_code=201, response_model=PostedRead)
def movement(
    business_id: UUID,
    loan_id: UUID,
    data: LoanMovementCreate,
    identity: Identity,
    db: DB,
) -> dict[str, object]:
    return service.loan_movement(db, identity, business_id, loan_id, data)
