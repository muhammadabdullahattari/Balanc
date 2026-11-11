"""Capture one immutable readiness workpaper for the last completed business day."""

import argparse
import json
from datetime import date, timedelta
from time import sleep
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.identity.models import User
from ledgerpilot.identity.security import now
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Membership
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.workspace import service
from ledgerpilot.workspace.models import ReportRun

KIND = "micro_close"


def report_id(business_id: UUID, as_of: date) -> UUID:
    return uuid5(NAMESPACE_URL, f"ledgerpilot:micro-close:v1:{business_id}:{as_of}")


def lookup(db: Session, business_id: UUID, as_of: date) -> ReportRun | None:
    return db.scalar(
        select(ReportRun).where(
            ReportRun.business_id == business_id,
            ReportRun.id == report_id(business_id, as_of),
            ReportRun.kind == KIND,
        )
    )


def capture(db: Session, actor: UUID, business_id: UUID) -> dict[str, Any]:
    set_context(db, actor)
    member = db.scalar(
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.business_id == business_id,
            Membership.user_id == actor,
            Membership.active.is_(True),
            Membership.role.in_(("owner", "accountant")),
            User.verified.is_(True),
            User.suspended.is_(False),
        )
    )
    if member is None:
        raise DomainError("ERR_NOT_FOUND", "Nightly close is unavailable.", 404)
    set_context(db, actor, business_id)
    business = get_business(db, business_id)
    as_of = now().astimezone(ZoneInfo(business.timezone)).date() - timedelta(days=1)
    previous = lookup(db, business_id, as_of)
    if previous is not None:
        return {"report_id": str(previous.id), "as_of": str(as_of), "created": False}

    identifier = report_id(business_id, as_of)
    result = service.readiness_report(db, business_id, as_of)
    result["report_id"] = str(identifier)
    result["kind"] = KIND
    inserted = db.scalar(
        insert(ReportRun)
        .values(
            id=identifier,
            business_id=business_id,
            kind=KIND,
            result=jsonable_encoder(result),
            created_by=actor,
        )
        .on_conflict_do_nothing(index_elements=[ReportRun.id])
        .returning(ReportRun.id)
    )
    if inserted is None:
        return {"report_id": str(identifier), "as_of": str(as_of), "created": False}
    service.store_report(db, actor, business_id, result["controls"])
    service.store_report(db, actor, business_id, result["tax_controls"])
    record(db, actor, "close.micro_close_captured", business_id, identifier)
    return {"report_id": str(identifier), "as_of": str(as_of), "created": True}


def capture_with_retry(
    sessions: sessionmaker[Session], actor: UUID, business_id: UUID
) -> dict[str, Any]:
    for attempt in range(3):
        try:
            with sessions.begin() as db:
                result = capture(db, actor, business_id)
            return result
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "40001" or attempt == 2:
                raise
    raise AssertionError("Micro-close retry loop exited unexpectedly")


def require_rollout(settings: Settings, business_id: UUID) -> None:
    if not settings.allows_p2_micro_close(business_id):
        raise DomainError("ERR_NOT_FOUND", "Nightly close is unavailable.", 404)


def run_once(settings: Settings, actor: UUID, business_id: UUID) -> dict[str, Any]:
    require_rollout(settings, business_id)
    engine = build_engine(settings).execution_options(isolation_level="REPEATABLE READ")
    try:
        return capture_with_retry(sessionmaker(engine), actor, business_id)
    finally:
        engine.dispose()


def watch(settings: Settings, actor: UUID, business_id: UUID) -> None:
    """Check the workspace day once a minute while the local worker is running."""
    require_rollout(settings, business_id)
    engine = build_engine(settings).execution_options(isolation_level="REPEATABLE READ")
    sessions = sessionmaker(engine)
    try:
        while True:
            try:
                result = capture_with_retry(sessions, actor, business_id)
                if result["created"]:
                    print(json.dumps(result), flush=True)
            except DBAPIError:
                print(json.dumps({"code": "ERR_DATABASE", "retry_seconds": 60}), flush=True)
            sleep(60)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", type=UUID, required=True)
    parser.add_argument("--business", type=UUID, required=True)
    parser.add_argument("--watch", action="store_true", help="Capture each completed workspace day")
    args = parser.parse_args()
    try:
        settings = Settings()
        if args.watch:
            watch(settings, args.actor, args.business)
        else:
            print(json.dumps(run_once(settings, args.actor, args.business)))
    except KeyboardInterrupt:
        return
    except DomainError as error:
        parser.exit(1, f"{error.code}: {error.message}\n")
    except DBAPIError:
        parser.exit(1, "ERR_DATABASE: The nightly workpaper could not be captured.\n")


if __name__ == "__main__":
    main()
