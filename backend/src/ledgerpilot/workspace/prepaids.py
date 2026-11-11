"""Prepare full-cost monthly prepaid schedules and post independently approved installments."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.identity.security import now
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import get_business

WRITERS = {"owner", "accountant", "bookkeeper"}


class Prepare(Schema):
    source_line_id: UUID
    expense_account_id: UUID
    description: str = Field(min_length=1, max_length=200, pattern=r"\S")
    first_month: date
    months: int = Field(ge=2, le=36)


class Recognize(ops.Command):
    due_date: date


class Recognition(Recognize):
    schedule_id: UUID


class Source(Schema):
    id: UUID
    accounting_date: date
    memo: str
    account_name: str
    amount: str


class Sources(Schema):
    items: list[Source]


class Installment(Schema):
    due_date: date
    amount: str
    journal_id: UUID | None


class Schedule(Schema):
    id: UUID
    description: str
    source_line_id: UUID
    prepared_by: UUID
    installments: list[Installment]


class Schedules(Schema):
    items: list[Schedule]


def monthly_allocations(
    amount: Decimal, precision: int, first_month: date, months: int
) -> list[tuple[date, Decimal]]:
    unit = Decimal(1).scaleb(-precision)
    if amount != amount.quantize(unit) or first_month.day != 1 or first_month.year > 9996:
        raise DomainError("ERR_PREPAID_SCHEDULE", "Choose a valid first month and currency amount.")
    whole, extra = divmod(int(amount / unit), months)
    if whole < 1:
        raise DomainError(
            "ERR_PREPAID_SCHEDULE", "Each month needs at least one minor currency unit."
        )
    rows = []
    for offset in range(months):
        years, month_index = divmod(first_month.month - 1 + offset, 12)
        year, month = first_month.year + years, month_index + 1
        rows.append(
            (date(year, month, monthrange(year, month)[1]), unit * (whole + int(offset < extra)))
        )
    return rows


_SOURCE = """
  SELECT l.id,j.accounting_date,j.memo,a.name AS account_name,l.debit::text AS amount
  FROM app.journal_lines l
  JOIN app.journals j ON j.business_id=l.business_id AND j.id=l.journal_id
  JOIN app.accounts a ON a.business_id=l.business_id AND a.id=l.account_id
  WHERE l.business_id=:b AND l.debit>0 AND a.active AND a.kind='asset' AND a.control IS NULL
    AND j.status='posted' AND j.source_kind='manual_journal' AND j.reversal_of IS NULL
    AND NOT EXISTS (SELECT 1 FROM app.journals r WHERE r.business_id=j.business_id
      AND r.reversal_of=j.id)
    AND NOT EXISTS (SELECT 1 FROM app.prepaid_schedules s WHERE s.business_id=l.business_id
      AND s.source_line_id=l.id)
"""


def sources(db: Session, bid: UUID) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            text(_SOURCE + " ORDER BY j.accounting_date DESC,l.id LIMIT 200"), {"b": bid}
        ).mappings()
    ]


def schedules(db: Session, bid: UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        text("""
          SELECT s.id,s.description,s.source_line_id,s.prepared_by,
            i.due_date,i.amount::text,i.journal_id
          FROM app.prepaid_schedules s JOIN app.prepaid_installments i
            ON i.business_id=s.business_id AND i.schedule_id=s.id
          WHERE s.business_id=:b ORDER BY s.id,i.due_date
        """),
        {"b": bid},
    ).mappings()
    result: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        if row["id"] not in result:
            result[row["id"]] = {
                key: row[key] for key in ("id", "description", "source_line_id", "prepared_by")
            }
            result[row["id"]]["installments"] = []
        result[row["id"]]["installments"].append(
            {key: row[key] for key in ("due_date", "amount", "journal_id")}
        )
    return list(result.values())


def prepare(db: Session, who: Principal, bid: UUID, data: Prepare) -> UUID:
    business = get_business(db, bid, lock=True)
    capped = db.scalar(
        text("SELECT 1 FROM app.prepaid_schedules WHERE business_id=:b OFFSET 99 LIMIT 1"),
        {"b": bid},
    )
    if capped:
        raise DomainError("ERR_QUOTA", "The local prepaid schedule limit is 100.", 413)
    source = (
        db.execute(
            text(_SOURCE + " AND l.id=:l"),
            {"b": bid, "l": data.source_line_id},
        )
        .mappings()
        .one_or_none()
    )
    if source is None:
        raise DomainError("ERR_PREPAID_SOURCE", "This prepaid source is unavailable.", 409)
    ops.account(db, bid, data.expense_account_id, {"expense"}, plain=True)
    rows = monthly_allocations(
        Decimal(source["amount"]), business.currency_precision, data.first_month, data.months
    )
    if rows[0][0] < source["accounting_date"]:
        raise DomainError("ERR_PREPAID_DATE", "Recognition cannot predate the source posting.")
    if db.scalar(
        text("""
          SELECT 1 FROM app.periods p
          WHERE p.business_id=:b AND p.locked AND EXISTS (
            SELECT 1 FROM unnest(CAST(:dates AS date[])) d
            WHERE d BETWEEN p.start_date AND p.end_date
          ) LIMIT 1
        """),
        {"b": bid, "dates": [day for day, _ in rows]},
    ):
        raise DomainError(
            "ERR_PERIOD_LOCKED", "The schedule overlaps a locked accounting period.", 409
        )
    sid: UUID = db.execute(
        text("""
          INSERT INTO app.prepaid_schedules(
            business_id,source_line_id,expense_account_id,prepared_by,description
          ) VALUES (:b,:l,:expense,:actor,:description) RETURNING id
        """),
        {
            "b": bid,
            "l": data.source_line_id,
            "expense": data.expense_account_id,
            "actor": who.user.id,
            "description": data.description.strip(),
        },
    ).scalar_one()
    db.execute(
        text("""
          INSERT INTO app.prepaid_installments(business_id,schedule_id,due_date,amount)
          VALUES (:b,:s,:d,:a)
        """),
        [{"b": bid, "s": sid, "d": day, "a": amount} for day, amount in rows],
    )
    record(db, who.user.id, "prepaid.prepared", bid, sid)
    return sid


def recognize(db: Session, who: Principal, bid: UUID, data: Recognition) -> dict[str, Any]:
    business, previous = ops.begin(db, who, bid, data, "prepaid.recognized")
    if previous is not None:
        return previous
    installment = (
        db.execute(
            text("""
          SELECT i.due_date,i.amount,s.expense_account_id,s.prepared_by,s.description,
            l.account_id AS asset_account_id
          FROM app.prepaid_schedules s JOIN app.prepaid_installments i
            ON i.business_id=s.business_id AND i.schedule_id=s.id
          JOIN app.journal_lines l ON l.business_id=s.business_id AND l.id=s.source_line_id
          WHERE s.business_id=:b AND s.id=:s AND i.journal_id IS NULL
          ORDER BY i.due_date LIMIT 1
        """),
            {"b": bid, "s": data.schedule_id},
        )
        .mappings()
        .one_or_none()
    )
    if installment is None or installment["due_date"] != data.due_date:
        raise DomainError(
            "ERR_PREPAID_ORDER", "Only the next unposted installment can be approved.", 409
        )
    if installment["prepared_by"] == who.user.id:
        raise DomainError("ERR_SEGREGATION", "A different authorized member must approve.", 403)
    if data.due_date > now().astimezone(ZoneInfo(business.timezone)).date():
        raise DomainError("ERR_PREPAID_DATE", "This installment is not due yet.", 409)
    amount = cast(Decimal, installment["amount"])
    expense = ops.account(db, bid, installment["expense_account_id"], {"expense"}, plain=True)
    asset = ops.account(db, bid, installment["asset_account_id"], {"asset"}, plain=True)
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.due_date,
        f"Prepaid recognition: {installment['description']}",
        [ops.line(expense.id, amount, debit=True), ops.line(asset.id, amount, debit=False)],
    )
    db.execute(
        text("""
          UPDATE app.prepaid_installments SET journal_id=:j
          WHERE business_id=:b AND schedule_id=:s AND due_date=:d
        """),
        {"b": bid, "s": data.schedule_id, "d": data.due_date, "j": jid},
    )
    return ops.finish(db, who, bid, data, "prepaid.recognized", {"id": str(jid)}, jid)
