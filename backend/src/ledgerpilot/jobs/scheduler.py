"""I1: Multi-tenant scheduled job runner with FOR UPDATE SKIP LOCKED leasing and idempotent runs."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context

ScheduledJobKind = Literal[
    "amortization_monthly",
    "accrual_month_end_scan",
    "accrual_day1_reversal",
    "flux_day1_analysis",
    "document_chase_24h",
]

DEFAULT_CRON_BY_KIND: dict[ScheduledJobKind, str] = {
    "amortization_monthly": "1 0 1 * *",
    "accrual_month_end_scan": "0 23 28-31 * *",
    "accrual_day1_reversal": "5 0 1 * *",
    "flux_day1_analysis": "15 0 1 * *",
    "document_chase_24h": "0 */6 * * *",
}


def compute_next_run(job_kind: ScheduledJobKind, from_dt: datetime | None = None) -> datetime:
    """Compute the next deterministic UTC trigger timestamp for a scheduled job kind."""
    now_dt = (from_dt or datetime.now(UTC)).astimezone(UTC)
    if job_kind == "document_chase_24h":
        return now_dt + timedelta(hours=6)
    year = now_dt.year + (1 if now_dt.month == 12 else 0)
    month = 1 if now_dt.month == 12 else now_dt.month + 1
    if job_kind == "accrual_month_end_scan":
        # Last day of current month at 23:00 UTC
        next_first = datetime(year, month, 1, 23, 0, tzinfo=UTC)
        last_day = next_first - timedelta(days=1)
        return last_day if last_day > now_dt else (datetime(year, month, 28, 23, 0, tzinfo=UTC))
    minute = (
        1
        if job_kind == "amortization_monthly"
        else (5 if job_kind == "accrual_day1_reversal" else 15)
    )
    return datetime(year, month, 1, 0, minute, tzinfo=UTC)


def upsert_scheduled_job(
    db: Session,
    *,
    business_id: UUID,
    actor_id: UUID,
    job_kind: ScheduledJobKind,
    cron_expr: str | None = None,
    timezone: str = "UTC",
    enabled: bool = True,
) -> dict[str, Any]:
    """Register or update a workspace scheduled automation job."""
    jid = uuid4()
    expr = (cron_expr or DEFAULT_CRON_BY_KIND[job_kind]).strip()
    next_at = compute_next_run(job_kind)
    row = (
        db.execute(
            text(
                """
                INSERT INTO app.scheduled_jobs (
                  id, business_id, job_kind, cron_expr, timezone,
                  next_run_at, enabled, created_by
                ) VALUES (
                  :id, :bid, :jk, :cron, :tz, :nxt, :en, :uid
                )
                ON CONFLICT (business_id, job_kind) DO UPDATE SET
                  cron_expr = EXCLUDED.cron_expr,
                  timezone = EXCLUDED.timezone,
                  enabled = EXCLUDED.enabled,
                  next_run_at = EXCLUDED.next_run_at
                RETURNING id, job_kind, cron_expr, timezone, next_run_at, last_run_at, enabled
                """
            ),
            {
                "id": jid,
                "bid": business_id,
                "jk": job_kind,
                "cron": expr,
                "tz": timezone,
                "nxt": next_at,
                "en": enabled,
                "uid": actor_id,
            },
        )
        .mappings()
        .one()
    )
    return {
        "id": str(row["id"]),
        "job_kind": str(row["job_kind"]),
        "cron_expr": str(row["cron_expr"]),
        "timezone": str(row["timezone"]),
        "next_run_at": row["next_run_at"].isoformat(),
        "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
        "enabled": bool(row["enabled"]),
    }


def list_scheduled_jobs(db: Session, business_id: UUID) -> list[dict[str, Any]]:
    """List scheduled automation jobs for the workspace."""
    rows = (
        db.execute(
            text(
                """
                SELECT id, job_kind, cron_expr, timezone, next_run_at, last_run_at, enabled
                FROM app.scheduled_jobs
                WHERE business_id = :bid
                ORDER BY job_kind ASC
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": str(r["id"]),
            "job_kind": str(r["job_kind"]),
            "cron_expression": str(r["cron_expr"]),
            "timezone": str(r["timezone"]),
            "next_run_at": r["next_run_at"].isoformat() if r["next_run_at"] else None,
            "last_run_at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
            "enabled": bool(r["enabled"]),
        }
        for r in rows
    ]


def lease_next_due_job(
    db: Session,
    *,
    business_id: UUID,
    lease_seconds: int = 120,
) -> dict[str, Any] | None:
    """Atomically lease one due job via FOR UPDATE SKIP LOCKED."""
    tok = f"sched-{uuid4().hex[:12]}"
    exp = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    row = (
        db.execute(
            text(
                """
                UPDATE app.scheduled_jobs
                SET lease_token = :tok,
                    lease_expires_at = :exp
                WHERE id = (
                  SELECT id
                  FROM app.scheduled_jobs
                  WHERE business_id = :bid
                    AND enabled = true
                    AND next_run_at <= now()
                    AND (lease_expires_at IS NULL OR lease_expires_at <= now())
                  ORDER BY next_run_at ASC
                  LIMIT 1
                  FOR UPDATE SKIP LOCKED
                )
                RETURNING id, business_id, job_kind, cron_expr, timezone, lease_token
                """
            ),
            {"tok": tok, "exp": exp, "bid": business_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "business_id": str(row["business_id"]),
        "job_kind": str(row["job_kind"]),
        "lease_token": str(row["lease_token"]),
    }


def record_scheduled_run(
    db: Session,
    *,
    business_id: UUID,
    job_id: UUID,
    job_kind: ScheduledJobKind,
    period_key: str,
    status: Literal["completed", "skipped", "failed"],
    items_processed: int = 0,
    summary: dict[str, Any] | None = None,
    error_code: str = "",
) -> dict[str, Any]:
    """Record an idempotent job execution and advance next_run_at while clearing the lease."""
    idem_key = f"{job_kind}:{business_id}:{period_key}"
    existing = db.scalar(
        text(
            "SELECT id FROM app.scheduled_job_runs "
            "WHERE business_id = :bid AND idempotency_key = :ik"
        ),
        {"bid": business_id, "ik": idem_key},
    )
    if existing is not None:
        return {"run_id": str(existing), "idempotent_replay": True, "status": "skipped"}

    rid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.scheduled_job_runs (
              id, business_id, job_id, job_kind, period_key, idempotency_key,
              status, items_processed, summary_json, error_code
            ) VALUES (
              :id, :bid, :jid, :jk, :pkey, :ik,
              :st, :cnt, CAST(:sum AS jsonb), :err
            )
            """
        ),
        {
            "id": rid,
            "bid": business_id,
            "jid": job_id,
            "jk": job_kind,
            "pkey": period_key,
            "ik": idem_key,
            "st": status,
            "cnt": items_processed,
            "sum": json.dumps(summary or {}),
            "err": error_code[:80],
        },
    )
    nxt = compute_next_run(job_kind)
    db.execute(
        text(
            """
            UPDATE app.scheduled_jobs
            SET last_run_at = now(),
                next_run_at = :nxt,
                lease_token = '',
                lease_expires_at = NULL
            WHERE business_id = :bid AND id = :jid
            """
        ),
        {"nxt": nxt, "bid": business_id, "jid": job_id},
    )
    return {
        "run_id": str(rid),
        "idempotent_replay": False,
        "status": status,
        "next_run_at": nxt.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LedgerPilot scheduled automation runner.")
    parser.add_argument("--actor", type=UUID, required=True)
    parser.add_argument("--business", type=UUID, required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings()
    engine = build_engine(settings)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            with sessions.begin() as db:
                set_context(db, args.actor, args.business)
                leased = lease_next_due_job(db, business_id=args.business)
                if leased is not None:
                    period_key = datetime.now(UTC).strftime("%Y-%m-%d")
                    out = record_scheduled_run(
                        db,
                        business_id=args.business,
                        job_id=UUID(leased["id"]),
                        job_kind=leased["job_kind"],
                        period_key=period_key,
                        status="completed",
                        summary={"leased_by": leased["lease_token"]},
                    )
                    print(json.dumps({"job": leased, "result": out}), flush=True)
            if not args.watch:
                break
            time.sleep(30)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
