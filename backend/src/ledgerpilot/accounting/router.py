"""Chart, periods, posting and exact ledger read APIs."""

from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select, tuple_

from ledgerpilot.accounting import drafts, repository, service, tax
from ledgerpilot.accounting.models import Account, Journal, JournalLine, Period
from ledgerpilot.accounting.schemas import (
    AccountCreate,
    AccountUpdate,
    DraftCreate,
    DraftPublish,
    DraftUpdate,
    JournalCreate,
    PeriodCreate,
    PeriodUpdate,
    ReversalCreate,
)
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import contracts
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.responses import (
    AccountRead,
    Identifier,
    JournalRead,
    Page,
    PeriodRead,
    TrialBalanceRead,
)
from ledgerpilot.tenancy.service import authorize, effective_policy, get_business

router = APIRouter(prefix="/businesses/{business_id}", tags=["accounting"])


@router.post(
    "/tax/remittances",
    status_code=201,
    response_model=contracts.responses["accounting.tax_remittance"],
    response_model_exclude_unset=True,
)
def tax_remittance(
    business_id: UUID,
    data: tax.Remittance,
    identity: Identity,
    db: DB,
) -> dict[str, object]:
    return tax.remit(business_id, data, identity, db)


@router.post(
    "/journal-drafts",
    status_code=201,
    response_model=contracts.responses["accounting.create_draft"],
    response_model_exclude_unset=True,
)
def create_draft(
    business_id: UUID, data: DraftCreate, identity: Identity, db: DB
) -> dict[str, object]:
    item = drafts.create(db, identity, business_id, data)
    return {"id": str(item.id), "version": item.version, "status": item.status}


@router.patch(
    "/journal-drafts/{journal_id}",
    response_model=contracts.responses["accounting.update_draft"],
    response_model_exclude_unset=True,
)
def update_draft(
    business_id: UUID, journal_id: UUID, data: DraftUpdate, identity: Identity, db: DB
) -> dict[str, object]:
    item = drafts.update(db, identity, business_id, journal_id, data)
    return {"id": str(item.id), "version": item.version, "status": item.status}


@router.post("/journal-drafts/{journal_id}/post", status_code=201, response_model=Identifier)
def publish_draft(
    business_id: UUID, journal_id: UUID, data: DraftPublish, identity: Identity, db: DB
) -> dict[str, str]:
    return {"id": str(drafts.publish(db, identity, business_id, journal_id, data))}


@router.post("/accounts", status_code=201, response_model=Identifier)
def create_account(
    business_id: UUID, data: AccountCreate, identity: Identity, db: DB
) -> dict[str, str]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    account = Account(business_id=business_id, **data.model_dump())
    db.add(account)
    db.flush()
    record(db, identity.user.id, "account.created", business_id, account.id)
    return {"id": str(account.id)}


@router.get("/accounts", response_model=Page[AccountRead])
def accounts(
    business_id: UUID,
    identity: Identity,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    cursor: UUID | None = None,
) -> dict[str, object]:
    authorize(db, identity, business_id)
    query = (
        select(Account)
        .where(Account.business_id == business_id)
        .order_by(Account.id)
        .limit(limit + 1)
    )
    if cursor:
        query = query.where(Account.id > cursor)
    rows = list(db.scalars(query))
    return {
        "items": [
            {
                "id": str(a.id),
                "code": a.code,
                "name": a.name,
                "kind": a.kind,
                "control": a.control,
                "active": a.active,
                "version": a.version,
            }
            for a in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.patch(
    "/accounts/{account_id}",
    response_model=contracts.responses["accounting.update_account"],
    response_model_exclude_unset=True,
)
def update_account(
    business_id: UUID, account_id: UUID, data: AccountUpdate, identity: Identity, db: DB
) -> dict[str, object]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    get_business(db, business_id, lock=True)
    account = repository.account(db, business_id, account_id)
    if not account:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    if account.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Refresh the account before updating.", 409)
    account.active = data.active
    account.version += 1
    record(db, identity.user.id, "account.updated", business_id, account.id)
    return {"id": str(account.id), "version": account.version, "active": account.active}


@router.post("/periods", status_code=201, response_model=Identifier)
def create_period(
    business_id: UUID, data: PeriodCreate, identity: Identity, db: DB
) -> dict[str, str]:
    authorize(db, identity, business_id, {"accountant"})
    get_business(db, business_id, lock=True)
    overlap = db.scalar(
        select(Period.id).where(
            Period.business_id == business_id,
            Period.start_date <= data.end_date,
            Period.end_date >= data.start_date,
        )
    )
    if overlap:
        raise DomainError("ERR_PERIOD_OVERLAP", "Periods cannot overlap.", 409)
    period = Period(business_id=business_id, **data.model_dump())
    db.add(period)
    db.flush()
    record(db, identity.user.id, "period.created", business_id, period.id)
    return {"id": str(period.id)}


@router.get("/periods", response_model=Page[PeriodRead])
def periods(
    business_id: UUID,
    identity: Identity,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    cursor: UUID | None = None,
) -> dict[str, object]:
    authorize(db, identity, business_id)
    query = (
        select(Period).where(Period.business_id == business_id).order_by(Period.id).limit(limit + 1)
    )
    if cursor:
        query = query.where(Period.id > cursor)
    rows = list(db.scalars(query))
    return {
        "items": [
            {
                "id": str(p.id),
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "locked": p.locked,
                "version": p.version,
                "status": p.status,
                "locked_by": str(p.locked_by) if p.locked_by else None,
            }
            for p in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.patch(
    "/periods/{period_id}",
    response_model=contracts.responses["accounting.lock_period"],
    response_model_exclude_unset=True,
)
def lock_period(
    business_id: UUID, period_id: UUID, data: PeriodUpdate, identity: Identity, db: DB
) -> dict[str, object]:
    authorize(db, identity, business_id, {"accountant"})
    get_business(db, business_id, lock=True)
    period = db.scalar(
        select(Period).where(Period.business_id == business_id, Period.id == period_id)
    )
    if not period:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    if period.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Refresh the period before updating.", 409)
    period.locked = data.locked
    period.version += 1
    record(
        db,
        identity.user.id,
        "period.locked" if data.locked else "period.unlocked",
        business_id,
        period.id,
    )
    return {"id": str(period.id), "locked": period.locked, "version": period.version}


@router.post("/journals", status_code=201, response_model=Identifier)
def post(business_id: UUID, data: JournalCreate, identity: Identity, db: DB) -> dict[str, str]:
    return {"id": str(service.post(db, identity, business_id, data))}


@router.get("/journals/{journal_id}", response_model=JournalRead)
def journal(business_id: UUID, journal_id: UUID, identity: Identity, db: DB) -> dict[str, object]:
    authorize(db, identity, business_id)
    item = repository.journal(db, business_id, journal_id)
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    reversed_by = repository.reversal_id(db, business_id, journal_id)
    return {
        "id": str(item.id),
        "accounting_date": item.accounting_date.isoformat(),
        "memo": item.memo,
        "currency": item.currency,
        "reversal_of": str(item.reversal_of) if item.reversal_of else None,
        "status": "reversed" if reversed_by else item.status,
        "reversed_by": str(reversed_by) if reversed_by else None,
        "version": item.version,
        "operation_key": str(item.operation_key),
        "period_id": str(item.period_id) if item.period_id else None,
        "policy_id": str(item.policy_id) if item.policy_id else None,
        "source_kind": item.source_kind,
        "source_id": str(item.source_id) if item.source_id else None,
        "approved_by": str(item.approved_by) if item.approved_by else None,
        "posted_at": item.posted_at.isoformat() if item.posted_at else None,
        "book": item.book,
        "lines": [
            {
                "account_id": str(line.account_id),
                "debit": str(line.debit),
                "credit": str(line.credit),
                "position": line.position,
            }
            for line in repository.lines(db, business_id, journal_id)
        ],
    }


@router.post("/journals/{journal_id}/reversal", status_code=201, response_model=Identifier)
def reverse(
    business_id: UUID, journal_id: UUID, data: ReversalCreate, identity: Identity, db: DB
) -> dict[str, str]:
    return {"id": str(service.reverse(db, identity, business_id, journal_id, data))}


@router.get(
    "/ledger",
    response_model=contracts.responses["accounting.ledger"],
    response_model_exclude_unset=True,
)
def ledger(
    business_id: UUID,
    identity: Identity,
    db: DB,
    start: date,
    end: date,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=140),
    account_id: UUID | None = None,
) -> dict[str, object]:
    authorize(db, identity, business_id)
    if start > end:
        raise DomainError("ERR_DATE", "Invalid date range.")
    query = select(Journal, JournalLine).join(
        JournalLine,
        (JournalLine.journal_id == Journal.id) & (JournalLine.business_id == Journal.business_id),
    )
    query = (
        query.where(
            Journal.business_id == business_id,
            Journal.status == "posted",
            Journal.accounting_date.between(start, end),
        )
        .order_by(Journal.accounting_date, Journal.id, JournalLine.position)
        .limit(limit + 1)
    )
    if cursor:
        try:
            cursor_date, cursor_journal, cursor_position = cursor.split("|")
            position = int(cursor_position)
            if not 1 <= position <= 200:
                raise ValueError
            key = (date.fromisoformat(cursor_date), UUID(cursor_journal), position)
        except (ValueError, TypeError):
            raise DomainError("ERR_CURSOR", "Invalid ledger cursor.") from None
        query = query.where(tuple_(Journal.accounting_date, Journal.id, JournalLine.position) > key)
    if account_id:
        query = query.where(JournalLine.account_id == account_id)
    rows = list(db.execute(query))
    b = get_business(db, business_id)
    return {
        "currency": b.currency,
        "timezone": b.timezone,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "report_type": "posted_general_ledger",
        "items": [
            {
                "id": str(line.id),
                "journal_id": str(j.id),
                "date": str(j.accounting_date),
                "account_id": str(line.account_id),
                "debit": str(line.debit),
                "credit": str(line.credit),
            }
            for j, line in rows[:limit]
        ],
        "next_cursor": (
            f"{rows[limit - 1][0].accounting_date}|{rows[limit - 1][0].id}|"
            f"{rows[limit - 1][1].position}"
        )
        if len(rows) > limit
        else None,
    }


@router.get("/trial-balance", response_model=TrialBalanceRead)
def trial_balance(business_id: UUID, identity: Identity, db: DB, as_of: date) -> dict[str, object]:
    authorize(db, identity, business_id)
    b = get_business(db, business_id)
    policy = effective_policy(db, b, as_of)
    rows = repository.trial_balance(db, business_id, as_of)
    with localcontext() as context:
        context.prec = 60
        debit = sum((Decimal(str(r["debit"])) for r in rows), Decimal(0))
        credit = sum((Decimal(str(r["credit"])) for r in rows), Decimal(0))
    return {
        "as_of": as_of.isoformat(),
        "currency": b.currency,
        "timezone": b.timezone,
        "policy_version": policy.version if policy else 1,
        "report_type": "posted_trial_balance",
        "items": rows,
        "total_debit": str(debit),
        "total_credit": str(credit),
        "balanced": debit == credit,
    }
