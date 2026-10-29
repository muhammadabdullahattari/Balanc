"""In-app notifications, close segregation and immutable cited report history."""

import csv
import io
from datetime import date
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Journal, JournalLine, Period
from ledgerpilot.audit.service import record
from ledgerpilot.banking.models import BankRow
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.reports.service import controls
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Membership
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.models import TradeDocument
from ledgerpilot.workspace.models import CloseTask, Evidence, Notification, Preference, ReportRun
from ledgerpilot.workspace.schemas import TaskUpdate


def save_report(db: Session, who: Principal, bid: UUID, result: dict[str, Any]) -> dict[str, Any]:
    saved = db.info.setdefault("saved_report_ids", set())
    if result["report_id"] in saved:
        return result
    saved.add(result["report_id"])
    for value in result.values():
        if isinstance(value, dict) and "report_id" in value:
            save_report(db, who, bid, value)
    encoded = jsonable_encoder(result)
    db.add(
        ReportRun(
            id=UUID(result["report_id"]),
            business_id=bid,
            kind=result.get("kind", result.get("basis", "report")),
            result=encoded,
            created_by=who.user.id,
        )
    )
    return result


def reminders(db: Session, who: Principal, bid: UUID, as_of: date) -> int:
    get_business(db, bid, lock=True)
    preference = db.scalar(
        select(Preference).where(
            Preference.business_id == bid,
            Preference.user_id == who.user.id,
        )
    )
    if preference and not preference.reminders:
        return 0
    balances = repo.due(db, bid, as_of)
    current = repo.due(db, bid, date.max)
    rows = list(
        db.scalars(
            select(TradeDocument)
            .where(
                TradeDocument.business_id == bid,
                TradeDocument.kind == "invoice",
                TradeDocument.due_date < as_of,
                TradeDocument.disputed.is_(False),
            )
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise DomainError("ERR_QUOTA", "The pilot reminder batch exceeds 10,000 invoices.", 413)
    created = 0
    for doc in rows:
        if balances.get(doc.id, 0) <= 0 or current.get(doc.id, 0) <= 0:
            continue
        result = db.execute(
            insert(Notification)
            .values(
                business_id=bid,
                recipient_id=who.user.id,
                kind="invoice_overdue",
                resource_id=doc.id,
                dedupe_key=f"invoice:{doc.id}:{as_of}",
            )
            .on_conflict_do_nothing()
            .returning(Notification.id)
        ).scalar_one_or_none()
        created += result is not None
    record(db, who.user.id, "reminders.reviewed", bid)
    return created


def update_task(
    db: Session,
    who: Principal,
    bid: UUID,
    tid: UUID,
    data: TaskUpdate,
) -> CloseTask:
    get_business(db, bid, lock=True)
    task = db.scalar(select(CloseTask).where(CloseTask.business_id == bid, CloseTask.id == tid))
    if not task:
        raise DomainError("ERR_NOT_FOUND", "Close task not found.", 404)
    period = db.scalar(select(Period).where(Period.business_id == bid, Period.id == task.period_id))
    if not period or period.locked:
        raise DomainError("ERR_PERIOD_LOCKED", "Closed tasks cannot change.", 409)
    if task.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Close task changed.", 409)
    member = db.scalar(
        select(Membership).where(
            Membership.business_id == bid,
            Membership.user_id == who.user.id,
            Membership.active.is_(True),
        )
    )
    if data.state == "approved":
        if (
            task.state != "prepared"
            or task.prepared_by == who.user.id
            or not member
            or member.role not in {"reviewer", "accountant", "owner"}
        ):
            raise DomainError(
                "ERR_SEGREGATION", "An independent reviewer must approve prepared work.", 403
            )
        task.reviewed_by = who.user.id
    elif data.state == "prepared":
        if task.assigned_to != who.user.id:
            raise DomainError("ERR_FORBIDDEN", "Only the assigned preparer may submit work.", 403)
        task.prepared_by, task.reviewed_by = who.user.id, None
    else:
        if member is None or member.role not in {"owner", "accountant"}:
            raise DomainError("ERR_FORBIDDEN", "Only an administrator may reopen work.", 403)
        task.prepared_by, task.reviewed_by = None, None
    if data.evidence_id:
        evidence(db, bid, data.evidence_id)
    task.state, task.evidence_id, task.comment = data.state, data.evidence_id, data.comment
    task.version += 1
    record(db, who.user.id, "close.task_updated", bid, tid)
    return task


def evidence(db: Session, bid: UUID, eid: UUID) -> Evidence:
    item = db.scalar(select(Evidence).where(Evidence.business_id == bid, Evidence.id == eid))
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Evidence not found.", 404)
    if item.status not in {"clean", "unsupported", "extracted"}:
        raise DomainError(
            "ERR_EVIDENCE_QUARANTINED", "Evidence has not passed malware scanning.", 409
        )
    return item


def readiness(db: Session, bid: UUID, as_of: date) -> dict[str, Any]:
    from ledgerpilot.reports.service import tax_support

    pending = (
        db.scalar(
            select(func.count())
            .select_from(BankRow)
            .where(
                BankRow.business_id == bid,
                BankRow.state == "for_review",
                BankRow.accounting_date <= as_of,
            )
        )
        or 0
    )
    tasks = (
        db.scalar(
            select(func.count())
            .select_from(CloseTask)
            .join(
                Period,
                (Period.id == CloseTask.period_id)
                & (Period.business_id == CloseTask.business_id),
            )
            .where(
                CloseTask.business_id == bid,
                Period.start_date <= as_of,
                CloseTask.required.is_(True),
                CloseTask.state != "approved",
            )
        )
        or 0
    )
    tieout = controls(db, bid, as_of)
    tax = tax_support(db, bid, date(1900, 1, 1), as_of)
    return {
        "as_of": as_of,
        "unreviewed_bank_rows": pending,
        "mandatory_tasks_pending": tasks,
        "controls": tieout,
        "tax_controls": tax,
        "ready": pending == 0 and tasks == 0 and tieout["balanced"] and tax["balanced"],
        "checks": ["unreviewed bank rows", "required close tasks", "AR/AP/loan control tie-outs"],
        "limitations": [
            "Document completeness and external source freshness require explicit evidence."
        ],
    }


def safe_cell(value: object) -> str:
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def journal_csv(db: Session, bid: UUID, start: date, end: date) -> str:
    if end < start:
        raise DomainError("ERR_DATE_RANGE", "Export dates must be ordered.")
    query = (
        select(
            Journal.id,
            Journal.accounting_date,
            Journal.memo,
            Journal.currency,
            JournalLine.position,
            JournalLine.account_id,
            JournalLine.debit,
            JournalLine.credit,
        )
        .join(
            JournalLine,
            (Journal.id == JournalLine.journal_id)
            & (Journal.business_id == JournalLine.business_id),
        )
        .where(
            Journal.business_id == bid,
            Journal.status == "posted",
            Journal.accounting_date >= start,
            Journal.accounting_date <= end,
        )
        .order_by(Journal.accounting_date, Journal.id, JournalLine.position)
        .limit(10001)
    )
    rows = list(db.execute(query))
    if len(rows) > 10000:
        raise DomainError("ERR_QUOTA", "Narrow the export to at most 10,000 journal lines.", 413)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["journal_id", "date", "memo", "currency", "position", "account_id", "debit", "credit"]
    )
    writer.writerows([[safe_cell(v) for v in row] for row in rows])
    return output.getvalue()
