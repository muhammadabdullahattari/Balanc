"""Project attribution and full-cost rebilling through the standard source draft flow."""

from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade import repository
from ledgerpilot.trade.schemas import DocumentCreate, SourceLine, exact_amount

WRITERS = {"owner", "accountant", "bookkeeper"}


class ProjectCreate(Schema):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    customer_id: UUID


class LineTag(Schema):
    project_id: UUID
    location: str | None = Field(default=None, max_length=120)
    billable: bool = False


class RebillDraft(Schema):
    source_line_id: UUID
    number: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    accounting_date: date
    due_date: date
    control_account_id: UUID
    income_account_id: UUID
    tax_amount: str = "0"
    tax_account_id: UUID | None = None
    tax_code: str | None = Field(default=None, max_length=40)
    idempotency_key: UUID
    _tax = field_validator("tax_amount")(exact_amount)

    @model_validator(mode="after")
    def check_dates(self) -> "RebillDraft":
        if self.due_date < self.accounting_date:
            raise ValueError("Due date precedes invoice date")
        return self


class ProjectRead(ProjectCreate):
    id: UUID


class ProjectList(Schema):
    items: list[ProjectRead]


class LedgerProjectLine(Schema):
    id: UUID
    accounting_date: date
    memo: str
    account_name: str
    debit: str
    credit: str
    project_id: UUID | None
    location: str | None
    billable: bool
    billing_state: str | None
    eligible: bool


class ProjectLedgerLines(Schema):
    items: list[LedgerProjectLine]


class ProjectTotals(Schema):
    income: str
    cost: str
    margin: str


def gate(settings: Settings, bid: UUID) -> None:
    if not settings.allows_p2_projects(bid):
        raise DomainError("ERR_NOT_FOUND", "Projects are unavailable in this workspace.", 404)


def list_projects(db: Session, bid: UUID) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT id,name,customer_id FROM app.projects "
                "WHERE business_id=:b ORDER BY name LIMIT 200"
            ),
            {"b": bid},
        ).mappings()
    ]


def project(db: Session, bid: UUID, pid: UUID) -> Any:
    row = (
        db.execute(
            text("SELECT id,name,customer_id FROM app.projects WHERE business_id=:b AND id=:p"),
            {"b": bid, "p": pid},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DomainError("ERR_NOT_FOUND", "Project not found.", 404)
    return row


def create_project(db: Session, who: Principal, bid: UUID, data: ProjectCreate) -> dict[str, Any]:
    get_business(db, bid, lock=True)
    repository.party(db, bid, data.customer_id, "customer")
    if db.scalar(
        text("SELECT 1 FROM app.projects WHERE business_id=:b OFFSET 199 LIMIT 1"), {"b": bid}
    ):
        raise DomainError("ERR_QUOTA", "The local project limit is 200.", 413)
    row = (
        db.execute(
            text("""
          INSERT INTO app.projects(business_id,name,customer_id) VALUES (:b,:n,:c)
          ON CONFLICT (business_id,name) DO NOTHING RETURNING id,name,customer_id
        """),
            {"b": bid, "n": data.name.strip(), "c": data.customer_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DomainError("ERR_PROJECT_NAME", "This project name is already in use.", 409)
    record(db, who.user.id, "project.created", bid, row["id"])
    return dict(row)


_LINE = """
  FROM app.journal_lines l
  JOIN app.journals j ON j.business_id=l.business_id AND j.id=l.journal_id
  JOIN app.accounts a ON a.business_id=l.business_id AND a.id=l.account_id
  LEFT JOIN app.trade_documents d ON d.business_id=l.business_id AND d.journal_id=j.id
  LEFT JOIN app.project_line_tags t ON t.business_id=l.business_id AND t.line_id=l.id
  LEFT JOIN app.billable_allocations ba ON ba.business_id=l.business_id
    AND ba.source_line_id=l.id AND ba.state<>'released'
"""
_ELIGIBLE = """
  a.kind='expense' AND l.debit>0 AND d.kind='bill' AND NOT d.disputed
  AND NOT EXISTS (SELECT 1 FROM app.trade_documents c WHERE c.business_id=l.business_id
    AND c.original_id=d.id)
  AND NOT EXISTS (SELECT 1 FROM app.journals r WHERE r.business_id=l.business_id
    AND r.reversal_of=j.id)
"""
_COLUMNS = f"""
  SELECT l.id,j.accounting_date,j.memo,a.name AS account_name,l.debit::text,l.credit::text,
    t.project_id,t.location,coalesce(t.billable,false) AS billable,ba.state AS billing_state,
    coalesce(({_ELIGIBLE}),false) AS eligible
"""


def ledger_lines(db: Session, bid: UUID, start: date, end: date) -> list[dict[str, Any]]:
    if end < start:
        raise DomainError("ERR_DATE", "Choose an ordered date range.")
    rows = (
        db.execute(
            text(
                _COLUMNS
                + _LINE
                + """
          WHERE l.business_id=:b AND j.status='posted' AND j.reversal_of IS NULL
            AND a.kind IN ('income','expense') AND j.accounting_date BETWEEN :s AND :e
          ORDER BY j.accounting_date DESC,l.id LIMIT 201
        """
            ),
            {"b": bid, "s": start, "e": end},
        )
        .mappings()
        .all()
    )
    if len(rows) > 200:
        raise DomainError("ERR_QUOTA", "Narrow the date range to at most 200 ledger lines.", 413)
    return [dict(row) for row in rows]


def _line(db: Session, bid: UUID, line_id: UUID) -> Any:
    row = (
        db.execute(
            text(
                _COLUMNS
                + _LINE
                + """
          WHERE l.business_id=:b AND l.id=:l AND j.status='posted' AND j.reversal_of IS NULL
            AND a.kind IN ('income','expense')
        """
            ),
            {"b": bid, "l": line_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DomainError("ERR_NOT_FOUND", "Posted income or expense line not found.", 404)
    return row


def tag_line(db: Session, who: Principal, bid: UUID, line_id: UUID, data: LineTag) -> None:
    get_business(db, bid, lock=True)
    project(db, bid, data.project_id)
    line = _line(db, bid, line_id)
    if line["project_id"] is not None:
        raise DomainError("ERR_PROJECT_TAG", "This ledger line already has a project tag.", 409)
    if data.billable and not line["eligible"]:
        raise DomainError("ERR_BILLABLE_SOURCE", "This line is unavailable for rebilling.", 409)
    db.execute(
        text("""
          INSERT INTO app.project_line_tags(business_id,line_id,project_id,location,billable)
          VALUES (:b,:l,:p,:loc,:billable)
        """),
        {
            "b": bid,
            "l": line_id,
            "p": data.project_id,
            "loc": data.location,
            "billable": data.billable,
        },
    )
    record(db, who.user.id, "project.line_tagged", bid, line_id)


def profitability(db: Session, bid: UUID, pid: UUID, start: date, end: date) -> dict[str, str]:
    project(db, bid, pid)
    if end < start:
        raise DomainError("ERR_DATE", "Choose an ordered date range.")
    # Include exact reversal lines using the journal position guaranteed by the ledger.
    row = db.execute(
        text("""
          WITH tagged AS (
            SELECT l.journal_id,l.position,l.debit,l.credit,a.kind,j.accounting_date
            FROM app.project_line_tags t
            JOIN app.journal_lines l ON l.business_id=t.business_id AND l.id=t.line_id
            JOIN app.accounts a ON a.business_id=l.business_id AND a.id=l.account_id
            JOIN app.journals j ON j.business_id=l.business_id AND j.id=l.journal_id
            WHERE t.business_id=:b AND t.project_id=:p AND j.status='posted'
          ), activity AS (
            SELECT debit,credit,kind FROM tagged WHERE accounting_date BETWEEN :s AND :e
            UNION ALL
            SELECT l.debit,l.credit,t.kind FROM tagged t
            JOIN app.journals j ON j.business_id=:b AND j.reversal_of=t.journal_id
            JOIN app.journal_lines l ON l.business_id=j.business_id AND l.journal_id=j.id
              AND l.position=t.position
            WHERE j.status='posted' AND j.accounting_date BETWEEN :s AND :e
          )
          SELECT coalesce(sum(credit-debit) FILTER (WHERE kind='income'),0),
            coalesce(sum(debit-credit) FILTER (WHERE kind='expense'),0) FROM activity
        """),
        {"b": bid, "p": pid, "s": start, "e": end},
    ).one()
    income, cost = cast(Decimal, row[0]), cast(Decimal, row[1])
    return {"income": str(income), "cost": str(cost), "margin": str(income - cost)}


def allocation(db: Session, bid: UUID, draft_id: UUID) -> Any:
    return (
        db.execute(
            text("""
          SELECT source_line_id,amount,state FROM app.billable_allocations
          WHERE business_id=:b AND draft_id=:d
        """),
            {"b": bid, "d": draft_id},
        )
        .mappings()
        .one_or_none()
    )


def draft_invoice(db: Session, who: Principal, bid: UUID, data: RebillDraft) -> dict[str, Any]:
    from ledgerpilot.trade import drafts

    business = get_business(db, bid, lock=True)
    line = _line(db, bid, data.source_line_id)
    if not line["billable"] or not line["eligible"]:
        raise DomainError("ERR_BILLABLE_SOURCE", "This line is unavailable for rebilling.", 409)
    if data.accounting_date < line["accounting_date"]:
        raise DomainError("ERR_DATE", "The draft invoice cannot predate the expense.")
    customer = project(db, bid, line["project_id"])["customer_id"]
    key = f"project-rebill-{data.idempotency_key}"
    previous = db.scalar(
        text("SELECT id FROM app.source_drafts WHERE business_id=:b AND key=:k"),
        {"b": bid, "k": key},
    )
    if previous is not None:
        reserved = allocation(db, bid, previous)
        if reserved is None or reserved["source_line_id"] != data.source_line_id:
            raise DomainError("ERR_IDEMPOTENCY_CONFLICT", "Draft key was already used.", 409)
    elif line["billing_state"] is not None:
        raise DomainError(
            "ERR_ALREADY_BILLABLE", "This expense is reserved or already billed.", 409
        )
    payload = DocumentCreate(
        kind="invoice",
        number=data.number,
        party_id=customer,
        accounting_date=data.accounting_date,
        due_date=data.due_date,
        control_account_id=data.control_account_id,
        expected_version=business.version,
        idempotency_key=key,
        lines=[
            SourceLine(
                account_id=data.income_account_id,
                description=data.description,
                amount=format(Decimal(line["debit"]), "f"),
                tax_amount=data.tax_amount,
                tax_account_id=data.tax_account_id,
                tax_code=data.tax_code,
            )
        ],
    )
    draft = drafts.create(db, who, bid, payload)
    if previous is None:
        db.execute(
            text("""
              INSERT INTO app.billable_allocations(business_id,source_line_id,draft_id,amount)
              VALUES (:b,:l,:d,:a)
            """),
            {
                "b": bid,
                "l": data.source_line_id,
                "d": UUID(draft["id"]),
                "a": Decimal(line["debit"]),
            },
        )
        record(db, who.user.id, "project.expense_reserved", bid, UUID(draft["id"]))
    return draft


def check_reserved_source(db: Session, bid: UUID, reserved: Any) -> Any:
    line = _line(db, bid, reserved["source_line_id"])
    if not line["eligible"] or not line["billable"]:
        raise DomainError(
            "ERR_BILLABLE_SOURCE", "This expense changed. Void the draft to release it.", 409
        )
    return line


def complete(db: Session, bid: UUID, draft_id: UUID, journal_id: UUID, source: Any) -> None:
    invoice_line: UUID = db.execute(
        text("""
          INSERT INTO app.project_line_tags(business_id,line_id,project_id,location,billable)
          SELECT business_id,id,:p,:loc,false FROM app.journal_lines
          WHERE business_id=:b AND journal_id=:j AND position=1
          RETURNING line_id
        """),
        {"b": bid, "j": journal_id, "p": source["project_id"], "loc": source["location"]},
    ).scalar_one()
    db.execute(
        text("""
          UPDATE app.billable_allocations SET state='billed', invoice_line_id=:l
          WHERE business_id=:b AND draft_id=:d AND state='reserved'
        """),
        {"b": bid, "d": draft_id, "l": invoice_line},
    )


def release(db: Session, bid: UUID, draft_id: UUID) -> None:
    db.execute(
        text("""
          UPDATE app.billable_allocations SET state='released'
          WHERE business_id=:b AND draft_id=:d AND state='reserved'
        """),
        {"b": bid, "d": draft_id},
    )
