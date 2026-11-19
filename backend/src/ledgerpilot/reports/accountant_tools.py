"""Local, read-only accountant tools. Monetary figures come from ledger reports."""

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import exists, func, select

from ledgerpilot.accounting.entry_tools import Entry, journal_preview
from ledgerpilot.accounting.models import Period
from ledgerpilot.accounting.schemas import DraftCreate
from ledgerpilot.banking.models import BankRow, Statement
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.reports import service
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.models import Party, TradeDocument
from ledgerpilot.workspace.models import CloseTask, EvidenceLink
from ledgerpilot.workspace.service import save_report

router = APIRouter(prefix="/businesses/{business_id}", tags=["accountant tools"])


class PortfolioStatus(Schema):
    pending_bank_rows: int
    oldest_transaction: date | None
    bills_without_evidence: int
    last_statement: date | None
    pending_period_reviews: int


@router.get("/portfolio-status")
def portfolio_status(business_id: UUID, identity: Identity, db: DB) -> PortfolioStatus:
    authorize(db, identity, business_id)
    missing = (
        select(func.count())
        .select_from(TradeDocument)
        .where(
            TradeDocument.business_id == business_id,
            TradeDocument.kind == "bill",
            ~exists().where(
                EvidenceLink.business_id == business_id,
                EvidenceLink.resource_kind == "trade_document",
                EvidenceLink.resource_id == TradeDocument.id,
            ),
        )
        .scalar_subquery()
    )
    business = get_business(db, business_id)
    last_statement = (
        select(func.max(Statement.as_of))
        .where(Statement.business_id == business_id)
        .scalar_subquery()
    )
    pending_reviews = (
        select(func.count())
        .select_from(CloseTask)
        .join(Period, Period.id == CloseTask.period_id)
        .where(
            CloseTask.business_id == business_id,
            Period.business_id == business_id,
            Period.end_date < func.date(func.timezone(business.timezone, func.now())),
            CloseTask.state == "prepared",
        )
        .scalar_subquery()
    )
    pending, oldest, bills, statement, reviews = db.execute(
        select(
            func.count(BankRow.id),
            func.min(BankRow.accounting_date),
            missing,
            last_statement,
            pending_reviews,
        ).where(BankRow.business_id == business_id, BankRow.state == "for_review")
    ).one()
    return PortfolioStatus(
        pending_bank_rows=pending,
        oldest_transaction=oldest,
        bills_without_evidence=bills,
        last_statement=statement,
        pending_period_reviews=reviews,
    )


class Question(Schema):
    question: str = Field(min_length=1, max_length=240)
    as_of: date = Field(ge=date(1901, 1, 1))
    basis: Literal["accrual", "cash"]


class Answer(Schema):
    answer: str
    report_ids: list[str]


def question_period(words: str, day: date) -> tuple[date, date] | None:
    if words.endswith("last month"):
        end = day.replace(day=1) - timedelta(days=1)
    elif words.endswith("this month"):
        end = day
    else:
        for month in range(1, 13):
            match = re.search(rf" in {calendar.month_name[month].lower()}(?: (\d{{4}}))?$", words)
            if match:
                year = int(match[1]) if match[1] else day.year - int(month > day.month)
                if not 1901 <= year <= day.year:
                    return None
                end = min(day, date(year, month, calendar.monthrange(year, month)[1]))
                if end.month != month:
                    return None
                break
        else:
            return None
    return end.replace(day=1), end


@router.post("/insights/question")
def insight(business_id: UUID, data: Question, identity: Identity, db: DB) -> Answer:
    authorize(db, identity, business_id)
    words = " ".join(data.question.lower().strip(" ?.").split())
    evidence: list[str] = []
    if words in {"which client owes us the most", "which customer owes us the most"}:
        aging = service.aging(db, business_id, data.as_of, "invoice")
        totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        for row in aging["items"]:
            totals[row["party_id"]] += Decimal(row["outstanding"])
        highest = max(totals.values(), default=Decimal(0))
        message = "No customer has a positive outstanding invoice balance."
        if highest > 0:
            ids = [UUID(key) for key, amount in totals.items() if amount == highest]
            names = db.scalars(
                select(Party.name)
                .where(Party.business_id == business_id, Party.id.in_(ids))
                .order_by(Party.name)
            )
            message = (
                f"Largest outstanding invoice balance: {highest} {aging['currency']} "
                f"({', '.join(names)})."
            )
        evidence.append(save_report(db, identity, business_id, aging)["report_id"])
        return Answer(answer=f"As of {data.as_of}. {message}", report_ids=evidence)
    period = question_period(words, data.as_of)
    period_pattern = r"(?:last month|this month|in [a-z]+(?: [0-9]{4})?)"
    profit = re.fullmatch(
        r"(?:what was|what is|show me) (?:our |the )?net profit " + period_pattern, words
    )
    expenses = re.fullmatch(
        r"(?:show me why expenses spiked|why did expenses (?:rise|increase|spike)) "
        + period_pattern,
        words,
    )
    if not period or not (profit or expenses):
        return Answer(
            answer="Ask about net profit last month, your top debtor, or monthly expenses.",
            report_ids=[],
        )
    start, end = period
    current = service.financial(db, business_id, start, end, data.basis, "profit-loss")
    evidence.append(save_report(db, identity, business_id, current)["report_id"])
    message = (
        f"Net profit from {start} through {end}: {current['net_profit']} "
        f"{current['currency']} ({data.basis} basis)."
    )
    if expenses:
        prior_end = start - timedelta(days=1)
        prior = service.financial(
            db, business_id, prior_end.replace(day=1), prior_end, data.basis, "profit-loss"
        )
        evidence.append(save_report(db, identity, business_id, prior)["report_id"])
        changes: defaultdict[str, Decimal] = defaultdict(Decimal)
        labels = {}
        for report, sign in ((current, 1), (prior, -1)):
            for row in report["items"]:
                if row["kind"] == "expense":
                    changes[row["account_id"]] += sign * Decimal(row["amount"])
                    labels[row["account_id"]] = row["name"]
        delta = Decimal(current["expense"]) - Decimal(prior["expense"])
        message = (
            f"Expenses from {start} through {end} changed by {delta} {current['currency']} "
            f"compared with {prior_end:%B %Y}."
        )
        if delta > 0 and changes:
            key = max(changes, key=lambda account: changes[account])
            message += f" Largest increase: {labels[key]} ({changes[key]} {current['currency']})."
    return Answer(answer=message, report_ids=evidence)


@router.post("/accounting/entry-preview")
def entry_preview(business_id: UUID, data: Entry, identity: Identity, db: DB) -> DraftCreate:
    authorize(db, identity, business_id, {"accountant", "owner", "bookkeeper"})
    business = get_business(db, business_id)
    return journal_preview(data, business.currency, business.currency_precision)
