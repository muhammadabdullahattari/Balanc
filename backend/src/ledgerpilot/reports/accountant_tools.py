"""Local, read-only accountant tools. Monetary figures come from ledger reports."""

import calendar
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import Field, ValidationError
from sqlalchemy import exists, func, select, text

from ledgerpilot.accounting.entry_tools import (
    Accrual,
    Entry,
    journal_preview,
    revaluation_delta,
    straight_line_installment,
)
from ledgerpilot.accounting.models import Account, Journal, Period
from ledgerpilot.accounting.schemas import DraftCreate
from ledgerpilot.banking.models import BankRow, Statement
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.reports import service
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.models import Membership
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
    requested = re.fullmatch(r"show me (revenue|expenses|net profit) " + period_pattern, words)
    comparison = re.fullmatch(
        r"compare (revenue|expenses|net profit) with previous quarter " + period_pattern, words
    )
    drill = re.fullmatch(
        r"show top (expense|expenses|revenue|net profit) accounts " + period_pattern, words
    )
    drivers = re.fullmatch(
        r"explain (net profit|expenses|revenue) change versus previous (month|quarter) "
        + period_pattern,
        words,
    )
    if not period or not (profit or expenses or requested or comparison or drill or drivers):
        # Natural-language causal variance question (e.g. "why is my profit lower this month?")
        is_causal_why = any(
            w in words
            for w in ("why", "lower", "drop", "dropped", "fell", "decreased", "changed", "higher")
        ) and any(
            m in words for m in ("profit", "income", "revenue", "expense", "expenses", "margin")
        )
        if is_causal_why:
            nl_start, nl_end = period if period else (data.as_of.replace(day=1), data.as_of)
            span_days = max(1, (nl_end - nl_start).days + 1)
            prior_end = nl_start - timedelta(days=1)
            prior_start = prior_end - timedelta(days=span_days - 1)
            cur_rep = service.financial(
                db, business_id, nl_start, nl_end, data.basis, "profit-loss"
            )
            prv_rep = service.financial(
                db, business_id, prior_start, prior_end, data.basis, "profit-loss"
            )
            evidence.append(save_report(db, identity, business_id, cur_rep)["report_id"])
            evidence.append(save_report(db, identity, business_id, prv_rep)["report_id"])

            cur_net = Decimal(cur_rep["net_profit"])
            prv_net = Decimal(prv_rep["net_profit"])
            net_diff = cur_net - prv_net
            ccy = cur_rep["currency"]

            acc_deltas: defaultdict[str, Decimal] = defaultdict(Decimal)
            acc_labels: dict[str, str] = {}
            for rep, sgn in ((cur_rep, 1), (prv_rep, -1)):
                for it in rep["items"]:
                    factor = -1 if it["kind"] == "expense" else 1
                    aid = str(it["account_id"])
                    acc_deltas[aid] += Decimal(it["amount"]) * sgn * factor
                    acc_labels[aid] = f"{it['code']} {it['name']}"

            top_drivers = sorted(
                (k for k in acc_deltas if acc_deltas[k] != Decimal("0")),
                key=lambda k: (-abs(acc_deltas[k]), k),
            )[:3]
            driver_text = (
                "; ".join(f"{acc_labels[k]} ({acc_deltas[k]:+.2f} {ccy})" for k in top_drivers)
                if top_drivers
                else "no account-level variance recorded"
            )

            top_docs = (
                db.execute(
                    text(
                        """
                        SELECT kind, number, total, accounting_date
                        FROM app.trade_documents
                        WHERE business_id = :b AND accounting_date BETWEEN :s AND :e
                        ORDER BY total DESC
                        LIMIT 3
                        """
                    ),
                    {"b": business_id, "s": nl_start, "e": nl_end},
                )
                .mappings()
                .all()
            )
            doc_citations = (
                " Notable source documents in period: "
                + ", ".join(
                    f"{d['kind']} {d['number']} "
                    f"({Decimal(str(d['total'])):.2f} {ccy} on {d['accounting_date']})"
                    for d in top_docs
                )
                + "."
                if top_docs
                else ""
            )

            direction = (
                "decreased"
                if net_diff < 0
                else ("increased" if net_diff > 0 else "remained unchanged")
            )
            return Answer(
                answer=(
                    f"Net profit from {nl_start} through {nl_end} is {cur_net:.2f} {ccy} "
                    f"({direction} by {abs(net_diff):.2f} {ccy} vs {prior_start} through "
                    f"{prior_end}: {prv_net:.2f} {ccy}). Top variance drivers: "
                    f"{driver_text}.{doc_citations}"
                ),
                report_ids=evidence,
            )

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
    if requested or comparison:
        selected = comparison or requested
        assert selected is not None
        label = selected[1]
        key = {"revenue": "income", "expenses": "expense", "net profit": "net_profit"}[label]
        currency = current["currency"]
        message = f"{label.capitalize()} from {start} through {end}: {current[key]} {currency}."
        if comparison:
            quarter_end = start.replace(month=1 + 3 * ((start.month - 1) // 3)) - timedelta(days=1)
            quarter_start = quarter_end.replace(month=quarter_end.month - 2, day=1)
            prior = service.financial(
                db, business_id, quarter_start, quarter_end, data.basis, "profit-loss"
            )
            evidence.append(save_report(db, identity, business_id, prior)["report_id"])
            message += f" Previous full quarter ({quarter_start} through {quarter_end}): "
            message += f"{prior[key]} {currency}."
    if drivers:
        metric, interval = drivers.groups()
        first = (
            start if interval == "month" else start.replace(month=1 + 3 * ((start.month - 1) // 3))
        )
        prior_end = first - timedelta(days=1)
        prior_start = prior_end.replace(
            day=1, month=prior_end.month - (2 if interval == "quarter" else 0)
        )
        prior = service.financial(
            db, business_id, prior_start, prior_end, data.basis, "profit-loss"
        )
        evidence.append(save_report(db, identity, business_id, prior)["report_id"])
        contributions: defaultdict[str, Decimal] = defaultdict(Decimal)
        account_names: dict[str, str] = {}
        for report, sign in ((current, 1), (prior, -1)):
            for item in report["items"]:
                if metric == "expenses" and item["kind"] != "expense":
                    continue
                if metric == "revenue" and item["kind"] != "income":
                    continue
                factor = -1 if metric == "net profit" and item["kind"] == "expense" else 1
                aid = item["account_id"]
                contributions[aid] += Decimal(item["amount"]) * sign * factor
                account_names[aid] = item["name"]
        ranked = sorted(
            (k for k in contributions if contributions[k]),
            key=lambda k: (-abs(contributions[k]), k),
        )
        details = "; ".join(f"{account_names[k]}: {contributions[k]:+f}" for k in ranked[:5])
        message = (
            f"Contributions to {metric} change ({current['currency']}), {start} through {end} "
            f"versus {prior_start} through {prior_end}: " + (details or "no account changed.")
        )
    if drill:
        kinds = (
            {"income", "expense"}
            if drill[1] == "net profit"
            else {"income" if drill[1] == "revenue" else "expense"}
        )
        rows = sorted(
            (row for row in current["items"] if row["kind"] in kinds and Decimal(row["amount"])),
            key=lambda row: (-abs(Decimal(row["amount"])), row["account_id"]),
        )[:5]
        details = "; ".join(f"{row['name']} ({row['kind']}): {row['amount']}" for row in rows)
        message = f"Largest account amounts for {start} through {end} ({current['currency']}): " + (
            details or "no activity."
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


class Assignment(Schema):
    assignee: UUID | None
    expected_assignee: UUID | None


ExceptionKind = Literal["bank", "bill", "close"]


@router.post("/portfolio-exceptions/{kind}/{source_id}/assignment")
def assign_exception(
    business_id: UUID,
    kind: ExceptionKind,
    source_id: UUID,
    data: Assignment,
    identity: Identity,
    db: DB,
) -> dict[str, UUID | None]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    if kind == "bank":
        target = select(BankRow.id).where(
            BankRow.business_id == business_id,
            BankRow.id == source_id,
            BankRow.state == "for_review",
        )
    elif kind == "bill":
        target = select(TradeDocument.id).where(
            TradeDocument.business_id == business_id,
            TradeDocument.id == source_id,
            TradeDocument.kind == "bill",
            ~exists().where(
                EvidenceLink.business_id == business_id,
                EvidenceLink.resource_kind == "trade_document",
                EvidenceLink.resource_id == source_id,
            ),
        )
    else:
        target = select(CloseTask.id).where(
            CloseTask.business_id == business_id,
            CloseTask.id == source_id,
            CloseTask.state == "prepared",
        )
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"assignment:{business_id}:{kind}:{source_id}"},
    )
    if db.scalar(target) is None:
        raise DomainError("ERR_NOT_FOUND", "This exception is no longer available.", 404)
    params = {"bid": business_id, "kind": kind, "source": source_id}
    current = db.scalar(
        text(
            "SELECT assigned_user FROM app.exception_assignments WHERE business_id=:bid AND "
            "kind=:kind AND source_id=:source ORDER BY event_id DESC LIMIT 1"
        ),
        params,
    )
    if data.assignee == current:
        return {"assignee": current}
    if data.expected_assignee != current:
        raise DomainError("ERR_CONFLICT", "The assignment changed. Refresh before retrying.", 409)
    if data.assignee is not None and not db.scalar(
        select(Membership.user_id).where(
            Membership.business_id == business_id,
            Membership.user_id == data.assignee,
            Membership.active.is_(True),
            Membership.role == "accountant",
        )
    ):
        raise DomainError("ERR_ASSIGNEE", "Choose an active accountant for this business.", 422)
    db.execute(
        text(
            "INSERT INTO "
            "app.exception_assignments(business_id,kind,source_id,actor,"
            "previous_user,assigned_user) "
            "VALUES (:bid,:kind,:source,:actor,:previous,:assigned)"
        ),
        {**params, "actor": identity.user.id, "previous": current, "assigned": data.assignee},
    )
    return {"assignee": data.assignee}


@router.get("/portfolio-exceptions/{kind}/{source_id}/assignment-history")
def assignment_history(
    business_id: UUID,
    kind: ExceptionKind,
    source_id: UUID,
    identity: Identity,
    db: DB,
    before: int | None = None,
) -> list[dict[str, object]]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    rows = db.execute(
        text(
            "SELECT event_id, actor, previous_user, assigned_user, changed_at FROM "
            "app.exception_assignments WHERE business_id=:bid AND kind=:kind AND "
            "source_id=:source AND event_id < COALESCE(:before,9223372036854775807) ORDER "
            "BY event_id DESC LIMIT 100"
        ),
        {"bid": business_id, "kind": kind, "source": source_id, "before": before},
    )
    return [dict(row) for row in rows.mappings()]


class Revaluation(Schema):
    period_end: date
    currency: Literal["USD", "GBP", "PKR", "AED", "EUR"]
    foreign_units: Decimal = Field(max_digits=18, decimal_places=6)
    carrying_balance: Decimal = Field(max_digits=18, decimal_places=6)
    closing_rate: Decimal = Field(gt=0, max_digits=16, decimal_places=8)
    rate_source: str = Field(min_length=1, max_length=80)
    monetary_account: UUID
    gain_account: UUID
    loss_account: UUID


@router.post("/accounting/revaluation-preview")
def revaluation_preview(
    business_id: UUID, data: Revaluation, identity: Identity, db: DB
) -> DraftCreate:
    authorize(db, identity, business_id, {"owner", "accountant"})
    business = get_business(db, business_id)
    if data.currency == business.currency:
        raise DomainError("ERR_CURRENCY", "Choose a foreign currency.", 422)
    if not db.scalar(
        select(Period.id).where(
            Period.business_id == business_id, Period.end_date == data.period_end
        )
    ):
        raise DomainError("ERR_PERIOD", "Choose an existing accounting period end.", 422)
    for account_id, kinds in (
        (data.monetary_account, ("asset", "liability")),
        (data.gain_account, ("income",)),
        (data.loss_account, ("expense",)),
    ):
        if not db.scalar(
            select(Account.id).where(
                Account.business_id == business_id,
                Account.id == account_id,
                Account.active.is_(True),
                Account.kind.in_(kinds),
            )
        ):
            raise DomainError(
                "ERR_ACCOUNT", "Choose an active business account of the required type.", 422
            )
    delta = revaluation_delta(
        data.foreign_units, data.closing_rate, data.carrying_balance, business.currency_precision
    )
    if not delta:
        raise DomainError(
            "ERR_AMOUNT", "The carrying balance already matches the closing value.", 422
        )
    entry = Accrual(
        kind="accrual",
        reference=f"FX revaluation {data.currency}: {data.rate_source}",
        accounting_date=data.period_end,
        amount=abs(delta),
        debit_account=data.monetary_account if delta > 0 else data.loss_account,
        credit_account=data.gain_account if delta > 0 else data.monetary_account,
    )
    proposed = journal_preview(entry, business.currency, business.currency_precision)
    params = {
        "b": business_id,
        "d": data.period_end,
        "a": data.monetary_account,
        "c": data.currency,
        "inputs": data.model_dump_json(),
        "draft": proposed.model_dump_json(),
        "actor": identity.user.id,
    }
    db.execute(
        text("""INSERT INTO app.fx_proposals
        (business_id,period_end,monetary_account,currency,inputs,draft,actor)
        VALUES (:b,:d,:a,:c,CAST(:inputs AS jsonb),CAST(:draft AS jsonb),:actor)
        ON CONFLICT DO NOTHING"""),
        params,
    )
    row = db.execute(
        text("""SELECT inputs,draft FROM app.fx_proposals
        WHERE business_id=:b AND period_end=:d AND monetary_account=:a AND currency=:c"""),
        params,
    ).one()
    if row.inputs != json.loads(data.model_dump_json()):
        raise DomainError(
            "ERR_CONFLICT", "A proposal with different inputs exists for this period.", 409
        )
    return DraftCreate.model_validate(row.draft)


class AssetInput(Schema):
    name: str = Field(min_length=1, max_length=160)
    acquired: date
    cost: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    residual: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    months: int = Field(ge=1, le=1200)


@router.post("/accounting/assets", status_code=201)
def create_asset(
    business_id: UUID, data: AssetInput, identity: Identity, db: DB
) -> dict[str, UUID]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    business = get_business(db, business_id)
    unit = Decimal(1).scaleb(-business.currency_precision)
    if data.residual >= data.cost or any(x != x.quantize(unit) for x in (data.cost, data.residual)):
        raise DomainError("ERR_AMOUNT", "Check the residual value and currency precision.", 422)
    asset_id = uuid4()
    db.execute(
        text("""INSERT INTO app.fixed_assets
        (id,business_id,name,acquired,cost,residual,months,actor)
        VALUES (:id,:b,:name,:acquired,:cost,:residual,:months,:actor)"""),
        {**data.model_dump(), "id": asset_id, "b": business_id, "actor": identity.user.id},
    )
    return {"id": asset_id}


class AssetEdit(Schema):
    name: str = Field(min_length=1, max_length=160)
    expected_version: int = Field(ge=1)
    disposed_on: date | None = None


@router.patch("/accounting/assets/{asset_id}")
def edit_asset(
    business_id: UUID, asset_id: UUID, data: AssetEdit, identity: Identity, db: DB
) -> dict[str, int]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    version = db.scalar(
        text("""UPDATE app.fixed_assets SET name=:name, disposed_on=:disposed_on,
        version=version+1,actor=:actor WHERE id=:id AND business_id=:b AND version=:expected_version
        AND disposed_on IS NULL AND (CAST(:disposed_on AS date) IS NULL OR acquired<=:disposed_on)
        RETURNING version"""),
        {**data.model_dump(), "actor": identity.user.id, "id": asset_id, "b": business_id},
    )
    if version is None:
        raise DomainError(
            "ERR_CONFLICT", "Check the disposal date and refresh the asset before retrying.", 409
        )
    return {"version": version}


@router.get("/accounting/assets")
def list_assets(
    business_id: UUID, identity: Identity, db: DB, after: UUID | None = None
) -> list[dict[str, object]]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    records = db.execute(
        text("""SELECT id,name,acquired,cost::text,residual::text,months,disposed_on,version
        FROM app.fixed_assets WHERE business_id=:b AND (CAST(:after AS uuid) IS NULL OR id>:after)
        ORDER BY id LIMIT 100"""),
        {"b": business_id, "after": after},
    )
    return [dict(r) for r in records.mappings()]


@router.get("/accounting/assets/{asset_id}/schedule")
def asset_schedule(
    business_id: UUID, asset_id: UUID, identity: Identity, db: DB
) -> list[dict[str, str]]:
    authorize(db, identity, business_id, {"accountant", "owner"})
    asset = db.execute(
        text("""SELECT acquired,cost,residual,months,disposed_on FROM app.fixed_assets
        WHERE id=:id AND business_id=:b"""),
        {"id": asset_id, "b": business_id},
    ).one_or_none()
    if asset is None:
        raise DomainError("ERR_NOT_FOUND", "Asset not found.", 404)
    unit = Decimal(1).scaleb(-get_business(db, business_id).currency_precision)
    schedule = []
    for index in range(asset.months):
        year, month = divmod(asset.acquired.year * 12 + asset.acquired.month - 1 + index, 12)
        if year > 9999:
            raise DomainError("ERR_PERIOD", "Schedule exceeds the supported calendar.", 422)
        end = date(year, month + 1, calendar.monthrange(year, month + 1)[1])
        if asset.disposed_on and end > asset.disposed_on:
            break
        amount = straight_line_installment(
            asset.cost - asset.residual, asset.months, index + 1, unit
        )
        schedule.append({"date": end.isoformat(), "amount": str(amount), "method": "straight-line"})
    return schedule


class QuoteLookup(Schema):
    day: date
    currency: Literal["USD", "GBP", "PKR", "AED", "EUR"]


class OverrideQuote(QuoteLookup):
    rate: Decimal = Field(gt=0, max_digits=16, decimal_places=8)
    source: str = Field(min_length=1, max_length=120)


class ProviderQuote(Schema):
    day: date
    base: str
    quote: str
    rate: Decimal = Field(gt=0, max_digits=16, decimal_places=8)


def fetch_quote(url: str, lookup: QuoteLookup, currency: str) -> Decimal:
    if not url.startswith("https://"):
        raise DomainError(
            "ERR_PROVIDER", "A rate provider is unavailable. Enter a documented rate.", 503
        )
    try:
        response = httpx.get(
            url,
            params={"day": lookup.day.isoformat(), "base": lookup.currency, "quote": currency},
            timeout=3,
            follow_redirects=False,
        )
        response.raise_for_status()
        quote = ProviderQuote.model_validate_json(response.content)
        if (quote.day, quote.base, quote.quote) != (lookup.day, lookup.currency, currency):
            raise ValueError("quote mismatch")
        return quote.rate
    except (httpx.HTTPError, ValidationError, ValueError):
        raise DomainError(
            "ERR_PROVIDER",
            "Could not verify the requested rate. Retry or enter a documented rate.",
            503,
        ) from None


@router.post("/accounting/fx-rates/lookup")
def quote_lookup(
    business_id: UUID, data: QuoteLookup, request: Request, identity: Identity, db: DB
) -> dict[str, str]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    currency = get_business(db, business_id).currency
    if currency == data.currency:
        raise DomainError("ERR_CURRENCY", "Choose a different currency.", 422)
    params = {"b": business_id, "d": data.day, "f": data.currency, "c": currency}
    row = db.execute(
        text("""SELECT rate,source FROM app.fx_quotes
        WHERE business_id=:b AND day=:d AND foreign_currency=:f AND base_currency=:c
        ORDER BY manual DESC,id DESC LIMIT 1"""),
        params,
    ).one_or_none()
    if row:
        return {"rate": str(row.rate), "source": row.source}
    rate = fetch_quote(request.app.state.settings.fx_provider_url, data, currency)
    db.execute(
        text("""INSERT INTO app.fx_quotes
        (business_id,day,foreign_currency,base_currency,rate,source,manual,actor)
        VALUES (:b,:d,:f,:c,:r,'Configured provider',false,:actor)"""),
        {**params, "r": rate, "actor": identity.user.id},
    )
    return {"rate": str(rate), "source": "Configured provider"}


@router.post("/accounting/fx-rates/override")
def manual_quote(
    business_id: UUID, data: OverrideQuote, identity: Identity, db: DB
) -> dict[str, str]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    currency = get_business(db, business_id).currency
    if data.currency == currency:
        raise DomainError("ERR_CURRENCY", "Choose a different currency.", 422)
    db.execute(
        text("""INSERT INTO app.fx_quotes
        (business_id,day,foreign_currency,base_currency,rate,source,manual,actor)
        VALUES (:b,:d,:f,:c,:r,:s,true,:actor)"""),
        {
            "b": business_id,
            "d": data.day,
            "f": data.currency,
            "c": currency,
            "r": data.rate,
            "s": data.source,
            "actor": identity.user.id,
        },
    )
    return {"rate": str(data.rate), "source": data.source}


class ExceptionItem(Schema):
    kind: ExceptionKind
    source_id: UUID
    description: str
    assignee: UUID | None


@router.get("/portfolio-exceptions", response_model=list[ExceptionItem])
def exception_list(
    business_id: UUID, identity: Identity, db: DB, offset: int = Query(0, ge=0)
) -> list[dict[str, object]]:
    authorize(db, identity, business_id, {"accountant", "owner"})
    rows = db.execute(
        text("""WITH items(kind,source_id,description) AS (
        SELECT 'bank',r.id,r.description FROM app.bank_rows r
        WHERE r.business_id=:b AND r.state='for_review'
        UNION ALL SELECT 'bill',d.id,d.number FROM app.trade_documents d
        WHERE d.business_id=:b AND d.kind='bill' AND NOT EXISTS
        (SELECT FROM app.evidence_links l WHERE l.business_id=:b
            AND l.resource_kind='trade_document'
            AND l.resource_id=d.id)
        UNION ALL SELECT 'close',t.id,t.name FROM app.close_tasks t
        JOIN app.periods p ON p.id=t.period_id AND p.business_id=:b
        JOIN app.businesses w ON w.id=:b
        WHERE t.business_id=:b AND t.state='prepared'
            AND p.end_date<timezone(w.timezone,now())::date)
        SELECT i.*,(SELECT a.assigned_user FROM app.exception_assignments a
        WHERE a.business_id=:b AND a.kind=i.kind AND a.source_id=i.source_id
        ORDER BY a.event_id DESC LIMIT 1) assignee FROM items i
        ORDER BY i.kind,i.source_id LIMIT 50 OFFSET :offset"""),
        {"b": business_id, "offset": offset},
    )
    return [dict(row) for row in rows.mappings()]


class AssetInstallment(Schema):
    number: int = Field(ge=1, le=1200)
    debit_account: UUID
    credit_account: UUID


@router.post("/accounting/assets/{asset_id}/installment-preview")
def asset_installment(
    business_id: UUID, asset_id: UUID, data: AssetInstallment, identity: Identity, db: DB
) -> DraftCreate:
    schedule = asset_schedule(business_id, asset_id, identity, db)
    if data.number > len(schedule):
        raise DomainError("ERR_SCHEDULE", "Choose an installment on the active schedule.", 422)
    row = schedule[data.number - 1]
    business = get_business(db, business_id)
    amount = Decimal(row["amount"])
    if not amount:
        raise DomainError(
            "ERR_AMOUNT", "This installment rounds to zero. No posting is needed.", 422
        )
    draft = journal_preview(
        Accrual(
            kind="accrual",
            reference=f"Asset {asset_id} installment {data.number}",
            accounting_date=date.fromisoformat(row["date"]),
            debit_account=data.debit_account,
            credit_account=data.credit_account,
            amount=amount,
        ),
        business.currency,
        business.currency_precision,
    )
    return draft


@router.get("/accounting/assets/{asset_id}/journals")
def asset_journals(
    business_id: UUID, asset_id: UUID, identity: Identity, db: DB
) -> list[dict[str, str]]:
    authorize(db, identity, business_id, {"accountant", "owner"})
    # The approval client uses a per-asset/installment operation key on the existing journal route.
    rows = db.execute(
        select(Journal.id, Journal.accounting_date, Journal.memo)
        .where(
            Journal.business_id == business_id,
            Journal.idempotency_key.startswith(f"asset.{asset_id}."),
            Journal.memo.startswith(f"Accrual — Asset {asset_id} "),
        )
        .order_by(Journal.accounting_date)
    )
    return [{"id": str(r.id), "date": r.accounting_date.isoformat(), "memo": r.memo} for r in rows]


@router.get("/accounting/fx-rates/history")
def quote_history(
    business_id: UUID, identity: Identity, db: DB, before: int | None = None
) -> list[dict[str, object]]:
    authorize(db, identity, business_id, {"accountant", "owner"})
    rows = db.execute(
        text("""SELECT id,day,foreign_currency,base_currency,rate::text,manual,
        source,recorded_at FROM app.fx_quotes WHERE business_id=:b
        AND id<coalesce(:before,9223372036854775807) ORDER BY id DESC LIMIT 100"""),
        {"b": business_id, "before": before},
    )
    return [dict(r) for r in rows.mappings()]


class AssetLifecycleEntry(Schema):
    action: Literal["acquisition", "disposal"]
    debit_account: UUID
    credit_account: UUID
    carrying_value: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)


@router.post("/accounting/assets/{asset_id}/lifecycle-preview")
def asset_lifecycle_preview(
    business_id: UUID, asset_id: UUID, data: AssetLifecycleEntry, identity: Identity, db: DB
) -> DraftCreate:
    authorize(db, identity, business_id, {"owner", "accountant"})
    asset = db.execute(
        text("""SELECT acquired, cost, disposed_on FROM app.fixed_assets
        WHERE id=:id AND business_id=:b"""),
        {"id": asset_id, "b": business_id},
    ).one_or_none()
    if asset is None:
        raise DomainError("ERR_NOT_FOUND", "Asset not found.", 404)
    if data.action == "disposal" and (asset.disposed_on is None or data.carrying_value is None):
        raise DomainError(
            "ERR_ASSET", "Record the disposal date and reviewed carrying value first.", 422
        )
    if data.action == "acquisition" and data.carrying_value is not None:
        raise DomainError("ERR_ASSET", "Acquisition uses the registered acquisition cost.", 422)
    business = get_business(db, business_id)
    return journal_preview(
        Accrual(
            kind="accrual",
            reference=f"Asset {asset_id} {data.action}",
            accounting_date=asset.acquired if data.action == "acquisition" else asset.disposed_on,
            amount=asset.cost if data.action == "acquisition" else data.carrying_value,
            debit_account=data.debit_account,
            credit_account=data.credit_account,
        ),
        business.currency,
        business.currency_precision,
    )
