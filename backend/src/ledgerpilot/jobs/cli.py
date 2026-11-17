"""Admin CLI tool to list, inspect, and replay background jobs."""

import argparse
import json
import sys
from uuid import UUID

from sqlalchemy.orm import Session

from ledgerpilot.config import get_settings
from ledgerpilot.database import build_engine
from ledgerpilot.jobs import service


def _list(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    with Session(engine) as db:
        bid = UUID(args.business) if args.business else None
        jobs = service.list_jobs(
            db,
            queue=args.queue,
            state=args.state,
            business_id=bid,
            limit=args.limit,
        )
        print(f"Found {len(jobs)} background jobs:")
        header = f"{'ID':<38} {'QUEUE':<10} {'KIND':<18} {'STATE':<10} {'ATTEMPTS':<10} {'CREATED'}"
        print(header)
        print("-" * len(header))
        for j in jobs:
            print(
                f"{str(j.id):<38} {j.queue:<10} {j.kind:<18} {j.state:<10} "
                f"{j.attempts}/{j.max_attempts:<7} {j.created_at.isoformat()}"
            )


def _inspect(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    with Session(engine) as db:
        job_id = UUID(args.job_id)
        from sqlalchemy import select

        from ledgerpilot.jobs.models import BackgroundJob

        job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))
        if job is None:
            print(f"Error: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        details = {
            "id": str(job.id),
            "business_id": str(job.business_id),
            "created_by": str(job.created_by),
            "queue": job.queue,
            "kind": job.kind,
            "idempotency_key": job.idempotency_key,
            "state": job.state,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "timeout_seconds": job.timeout_seconds,
            "available_at": job.available_at.isoformat() if job.available_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "payload": job.payload,
            "result": job.result,
            "error_detail": job.error_detail,
        }
        print(json.dumps(details, indent=2))


def _replay(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    with Session(engine) as db:
        job_id = UUID(args.job_id)
        ok = service.replay_job(db, job_id)
        db.commit()
        if ok:
            print(f"Job {job_id} successfully replayed and reset to 'queued'.")
        else:
            msg = f"Failed to replay job {job_id} (must be in 'failed' or 'dead_letter' state)."
            print(msg, file=sys.stderr)
            sys.exit(1)


def _replay_all(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    with Session(engine) as db:
        jobs = service.list_jobs(db, queue=args.queue, state="dead_letter", limit=500)
        replayed = 0
        for j in jobs:
            if service.replay_job(db, j.id):
                replayed += 1
        db.commit()
        print(f"Replayed {replayed} dead-letter jobs in queue '{args.queue}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description="LedgerPilot Background Jobs Admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    list_p = subparsers.add_parser("list", help="List background jobs")
    list_p.add_argument("--queue", help="Filter by queue name")
    list_p.add_argument(
        "--state",
        help="Filter by state (queued, running, succeeded, failed, dead_letter)",
    )
    list_p.add_argument("--business", help="Filter by business UUID")
    list_p.add_argument("--limit", type=int, default=50, help="Max jobs to return")
    list_p.set_defaults(func=_list)

    # inspect
    inspect_p = subparsers.add_parser("inspect", help="Inspect a specific background job")
    inspect_p.add_argument("job_id", help="Job UUID to inspect")
    inspect_p.set_defaults(func=_inspect)

    # replay
    replay_p = subparsers.add_parser("replay", help="Replay a failed or dead-letter job")
    replay_p.add_argument("job_id", help="Job UUID to replay")
    replay_p.set_defaults(func=_replay)

    # replay-all
    replay_all_p = subparsers.add_parser(
        "replay-all", help="Replay all dead-letter jobs in a queue"
    )
    replay_all_p.add_argument("--queue", default="default", help="Queue name")
    replay_all_p.set_defaults(func=_replay_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
