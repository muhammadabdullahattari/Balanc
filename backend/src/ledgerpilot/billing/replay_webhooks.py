"""CLI tool to list, inspect, and replay stored Stripe webhook events."""

import argparse
import json
import sys
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.config import get_settings
from ledgerpilot.database import build_engine
from ledgerpilot.jobs import service as job_service
from ledgerpilot.provider_integrations import process_stripe_event


def list_events(
    db: Session,
    *,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List stored Stripe webhook events with optional filters."""
    query = (
        "SELECT id, event_type, status, received_at, processed_at, error_detail "
        "FROM app.stripe_event_inbox WHERE 1=1"
    )
    params: dict[str, Any] = {"limit": limit}
    if status:
        query += " AND status = :status"
        params["status"] = status
    if event_type:
        query += " AND event_type = :event_type"
        params["event_type"] = event_type
    query += " ORDER BY received_at DESC LIMIT :limit"

    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def inspect_event(db: Session, event_id: str) -> dict[str, Any] | None:
    """Inspect full payload and execution metadata for a webhook event."""
    row = (
        db.execute(
            text(
                "SELECT id, event_type, status, received_at, processed_at, "
                "error_detail, payload FROM app.stripe_event_inbox WHERE id = :id"
            ),
            {"id": event_id},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def replay_event(
    db: Session,
    event_id: str,
    *,
    sync: bool = False,
) -> bool:
    """Replay a stored Stripe webhook event synchronously or via background job."""
    event_row = inspect_event(db, event_id)
    if not event_row:
        return False

    payload = event_row.get("payload")
    if not payload:
        payload = {"id": event_id, "type": event_row["event_type"]}

    settings = get_settings()

    if sync:
        try:
            process_stripe_event(db, settings, payload)
            db.execute(
                text("SELECT app.stripe_record_event_result(:id, 'processed')"),
                {"id": event_id},
            )
            db.commit()
            return True
        except Exception as exc:
            db.execute(
                text("SELECT app.stripe_record_event_result(:id, 'failed', :err)"),
                {"id": event_id, "err": str(exc)},
            )
            db.commit()
            raise

    # Asynchronous replay via background job queue
    db.execute(
        text(
            "UPDATE app.stripe_event_inbox "
            "SET status = 'received', error_detail = NULL WHERE id = :id"
        ),
        {"id": event_id},
    )
    job_service.enqueue(
        db,
        business_id=None,
        created_by=None,
        queue="webhook",
        kind="stripe_webhook",
        payload={"event_id": event_id, "event": payload},
        idempotency_key=f"stripe_webhook:{event_id}:replay:{uuid4().hex[:8]}",
        max_attempts=settings.jobs_default_max_attempts,
    )
    db.commit()
    return True


def replay_failed_events(db: Session, *, sync: bool = False) -> int:
    """Replay all failed webhook events."""
    rows: list[str] = list(
        db.execute(
            text(
                "SELECT id FROM app.stripe_event_inbox "
                "WHERE status = 'failed' ORDER BY received_at ASC"
            )
        ).scalars().all()
    )
    count = 0
    for eid in rows:
        if replay_event(db, str(eid), sync=sync):
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ledgerpilot.billing.replay_webhooks",
        description="List, inspect, and replay stored Stripe webhook events.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    list_cmd = subparsers.add_parser("list", help="List stored webhook events")
    list_cmd.add_argument(
        "--status", choices=["received", "processed", "failed"], help="Filter by status"
    )
    list_cmd.add_argument("--event-type", help="Filter by Stripe event type")
    list_cmd.add_argument("--limit", type=int, default=50, help="Maximum events to return")

    # inspect
    inspect_cmd = subparsers.add_parser("inspect", help="Inspect a specific webhook event")
    inspect_cmd.add_argument("event_id", help="Stripe event ID (evt_...)")

    # replay
    replay_cmd = subparsers.add_parser("replay", help="Replay a specific webhook event")
    replay_cmd.add_argument("event_id", help="Stripe event ID (evt_...)")
    replay_cmd.add_argument("--sync", action="store_true", help="Execute replay synchronously")

    # replay-failed
    replay_failed_cmd = subparsers.add_parser(
        "replay-failed", help="Replay all failed webhook events"
    )
    replay_failed_cmd.add_argument(
        "--sync", action="store_true", help="Execute replays synchronously"
    )

    args = parser.parse_args(argv)
    settings = get_settings()
    engine = build_engine(settings)

    with Session(engine) as db:
        if args.command == "list":
            events = list_events(
                db, status=args.status, event_type=args.event_type, limit=args.limit
            )
            if not events:
                print("No webhook events found matching filter.")
                return 0
            header = f"{'Event ID':<32} {'Type':<32} {'Status':<12} {'Received At':<24} {'Error'}"
            print(header)
            print("-" * len(header))
            for ev in events:
                err = (ev.get("error_detail") or "")[:30]
                print(
                    f"{ev['id']:<32} {ev['event_type']:<32} {ev['status']:<12} "
                    f"{str(ev['received_at']):<24} {err}"
                )
            print(f"\nTotal: {len(events)} events")

        elif args.command == "inspect":
            event = inspect_event(db, args.event_id)
            if not event:
                print(f"Error: Webhook event '{args.event_id}' not found.", file=sys.stderr)
                return 1
            print(f"Event ID:    {event['id']}")
            print(f"Type:        {event['event_type']}")
            print(f"Status:      {event['status']}")
            print(f"Received At: {event['received_at']}")
            print(f"Processed:   {event['processed_at']}")
            print(f"Error:       {event['error_detail']}")
            print("\nPayload:")
            print(json.dumps(event.get("payload") or {}, indent=2))

        elif args.command == "replay":
            success = replay_event(db, args.event_id, sync=args.sync)
            if not success:
                print(f"Error: Event '{args.event_id}' not found.", file=sys.stderr)
                return 1
            mode = "sync" if args.sync else "queued"
            print(f"Event '{args.event_id}' replayed successfully ({mode}).")

        elif args.command == "replay-failed":
            replayed = replay_failed_events(db, sync=args.sync)
            print(f"Replayed {replayed} failed webhook event(s).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
