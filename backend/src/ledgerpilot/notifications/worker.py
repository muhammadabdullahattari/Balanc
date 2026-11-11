"""Process one scoped workspace email job against the local Mailpit transport."""

import argparse
import json
import logging
import smtplib
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.identity.models import User
from ledgerpilot.identity.security import now
from ledgerpilot.notifications import email_outbox as outbox
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import emit
from ledgerpilot.tenancy.models import Membership
from ledgerpilot.tenancy.service import get_business

Job = outbox.BusinessEmailJob
Outcome = Literal["sent", "retry", "failed", "uncertain"]


@dataclass(frozen=True)
class Delivery:
    job_id: UUID
    recipient: str
    kind: str
    count: int


def _scope(db: Session, bid: UUID, actor: UUID) -> None:
    set_context(db, actor)
    membership = db.scalar(
        select(Membership).where(
            Membership.business_id == bid,
            Membership.user_id == actor,
            Membership.active.is_(True),
            Membership.role.in_(outbox.ROLES),
        )
    )
    if membership is None:
        raise DomainError("ERR_NOT_FOUND", "Workspace email jobs are unavailable.", 404)
    set_context(db, actor, bid)


def _claim(db: Session, settings: Settings, bid: UUID, actor: UUID) -> Delivery | None:
    _scope(db, bid, actor)
    at = now()
    scope = (Job.business_id == bid, Job.recipient_id == actor)
    # A previous process could have sent DATA before it died. Never reclaim for resending.
    db.execute(
        update(Job)
        .where(*scope, Job.state == "sending", Job.updated_at <= at - outbox.SEND_WINDOW)
        .values(state="uncertain", updated_at=at, outcome_code="ERR_SEND_INTERRUPTED")
    )
    job = db.scalar(
        select(Job)
        .where(*scope, Job.state == "queued", Job.available_at <= at)
        .order_by(Job.available_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.updated_at = at
    if outbox.suppressed(db, bid, actor):
        job.state, job.outcome_code = "suppressed", "ERR_DELIVERY_SUPPRESSED"
        return None
    preference = outbox.preferences(db, bid, actor)
    recipient = db.get(User, actor)
    if (
        not outbox.local_delivery_available(settings, bid)
        or preference is None
        or not outbox.opted_in(preference)
        or recipient is None
        or not recipient.verified
        or recipient.suspended
        or job.created_at <= at - outbox.MAX_AGE
    ):
        job.state, job.outcome_code = "suppressed", "ERR_DELIVERY_INELIGIBLE"
        return None
    if outbox.is_quiet(at, get_business(db, bid), preference):
        job.available_at = at + timedelta(minutes=15)
        job.outcome_code = "DEFERRED_QUIET_HOURS"
        return None
    count = outbox.eligible_count(db, bid, job.review_date, job.kind)
    if count == 0:
        job.state, job.outcome_code = "suppressed", "NO_REMAINING_ITEMS"
        return None
    job.state, job.outcome_code = "sending", None
    job.attempts += 1
    return Delivery(job.id, recipient.email, job.kind, count)


def _send(settings: Settings, delivery: Delivery) -> tuple[Outcome, str | None]:
    subject = (
        "Overdue invoices to review"
        if delivery.kind == "overdue_invoices"
        else "Overdue invoices missing supporting evidence"
    )
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = delivery.recipient
    message["Subject"] = f"LedgerPilot | {subject}"
    message["Message-ID"] = f"<{delivery.job_id}@ledgerpilot.local>"
    message.set_content(
        f"{subject}\n\n"
        f"Items to review: {delivery.count}\n"
        "Open your LedgerPilot workspace to review the records.\n"
        "Manage workspace email under Activity and notifications.\n"
    )
    try:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5)
    except (OSError, smtplib.SMTPException):
        return "retry", "ERR_SMTP_CONNECT"
    try:
        client.send_message(message, to_addrs=[delivery.recipient])
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as error:
        return ("retry" if 400 <= error.smtp_code < 500 else "failed"), "ERR_SMTP_REJECTED"
    except smtplib.SMTPRecipientsRefused as error:
        status = error.recipients.get(delivery.recipient, (500, b""))[0]
        return ("retry" if 400 <= status < 500 else "failed"), "ERR_SMTP_REJECTED"
    except (OSError, smtplib.SMTPException):
        return "uncertain", "ERR_SMTP_OUTCOME_UNKNOWN"
    finally:
        # A connection close after a successful DATA response cannot undo acceptance.
        with suppress(OSError):
            client.close()
    return "sent", None


def run_once(settings: Settings, actor: UUID, business_id: UUID) -> dict[str, Any]:
    engine = build_engine(settings)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions.begin() as db:
            delivery = _claim(db, settings, business_id, actor)
        if delivery is None:
            return {"processed": False}
        # The sending state is durable before starting external I/O.
        outcome, code = _send(settings, delivery)
        with sessions.begin() as db:
            _scope(db, business_id, actor)
            job = db.scalar(
                select(Job)
                .where(
                    Job.id == delivery.job_id,
                    Job.business_id == business_id,
                    Job.recipient_id == actor,
                )
                .with_for_update()
            )
            if job is None or job.state != "sending":
                return {"processed": True, "job_id": str(delivery.job_id), "outcome": "uncertain"}
            job.updated_at = now()
            if outcome == "retry" and job.attempts < outbox.ATTEMPT_LIMIT:
                job.state = "queued"
                job.available_at = job.updated_at + timedelta(seconds=30 * 2 ** (job.attempts - 1))
            else:
                job.state = "failed" if outcome == "retry" else outcome
            job.outcome_code = code
        emit(
            "business_email.delivery_result",
            business_id=business_id,
            user_id=actor,
            job_id=delivery.job_id,
            stage=job.state,
            code=code,
        )
        return {"processed": True, "job_id": str(delivery.job_id), "outcome": job.state}
    finally:
        engine.dispose()


def status(settings: Settings, actor: UUID, business_id: UUID) -> list[dict[str, Any]]:
    engine = build_engine(settings)
    try:
        with sessionmaker(engine).begin() as db:
            _scope(db, business_id, actor)
            return outbox.history(db, business_id, actor)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", type=UUID, required=True)
    parser.add_argument("--business", type=UUID, required=True)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    action = status if args.status else run_once
    print(json.dumps(action(settings, args.actor, args.business)))


if __name__ == "__main__":
    main()
