"""Daily, cited cash-shortfall notifications using the existing in-app inbox."""

import argparse
import hashlib
import json
from time import sleep
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.identity.models import User
from ledgerpilot.identity.security import now
from ledgerpilot.notifications.email_outbox import is_quiet
from ledgerpilot.reports.service import forecast
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Membership
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.workspace.models import Notification, Preference
from ledgerpilot.workspace.service import store_report


def capture(db: Session, actor: UUID, bid: UUID, accounts: list[UUID]) -> dict[str, Any]:
    set_context(db, actor)
    active = db.scalar(
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == actor,
            Membership.business_id == bid,
            Membership.active.is_(True),
            Membership.role.in_(("owner", "accountant")),
            User.verified.is_(True),
            User.suspended.is_(False),
        )
    )
    if active is None:
        raise DomainError("ERR_NOT_FOUND", "Cash alerts are unavailable.", 404)
    set_context(db, actor, bid)
    business = get_business(db, bid, lock=True)
    preference = db.scalar(
        select(Preference).where(Preference.business_id == bid, Preference.user_id == actor)
    )
    at = now()
    if preference is None or not preference.reminders or is_quiet(at, business, preference):
        return {"created": False, "reason": "preferences_or_quiet_hours"}
    day = at.astimezone(ZoneInfo(business.timezone)).date()
    account_key = hashlib.sha256(",".join(sorted(map(str, accounts))).encode()).hexdigest()[:24]
    key = f"cash-shortfall:v1:{day}:{account_key}"
    previous = db.scalar(
        select(Notification).where(
            Notification.business_id == bid,
            Notification.recipient_id == actor,
            Notification.dedupe_key == key,
        )
    )
    if previous:
        return {
            "created": False,
            "reason": "already_notified",
            "report_id": str(previous.resource_id),
        }
    result = forecast(db, bid, day, accounts)
    first = next((week for week in result["weeks"] if week["shortfall"]), None)
    if first is None:
        return {"created": False, "reason": "no_shortfall"}
    store_report(db, actor, bid, result)
    identifier = db.scalar(
        insert(Notification)
        .values(
            business_id=bid,
            recipient_id=actor,
            kind="cash_shortfall",
            resource_id=UUID(result["report_id"]),
            dedupe_key=key,
        )
        .on_conflict_do_nothing()
        .returning(Notification.id)
    )
    record(db, actor, "forecast.shortfall_notified", bid, identifier)
    return {
        "created": identifier is not None,
        "report_id": result["report_id"],
        "first_shortfall": str(first["end_date"]),
    }


def run_once(settings: Settings, actor: UUID, bid: UUID, accounts: list[UUID]) -> dict[str, Any]:
    if not settings.allows_p2_insights(bid):
        raise DomainError("ERR_NOT_FOUND", "Cash alerts are unavailable.", 404)
    if not 1 <= len(accounts) <= 20 or len(set(accounts)) != len(accounts):
        raise DomainError(
            "ERR_ACCOUNT", "Select between one and twenty distinct cash accounts.", 422
        )
    engine = build_engine(settings)
    try:
        with sessionmaker(engine).begin() as db:
            return capture(db, actor, bid, accounts)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", type=UUID, required=True)
    parser.add_argument("--business", type=UUID, required=True)
    parser.add_argument("--cash-account", type=UUID, action="append", required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings()
        while True:
            try:
                print(
                    json.dumps(run_once(settings, args.actor, args.business, args.cash_account)),
                    flush=True,
                )
            except DBAPIError:
                if not args.watch:
                    raise
                print(json.dumps({"code": "ERR_DATABASE", "retry_seconds": 300}), flush=True)
            if not args.watch:
                break
            sleep(300)
    except KeyboardInterrupt:
        return
    except DomainError as error:
        parser.exit(1, f"{error.code}: {error.message}\n")
    except DBAPIError:
        parser.exit(1, "ERR_DATABASE: Cash alerts could not be checked.\n")


if __name__ == "__main__":
    main()
