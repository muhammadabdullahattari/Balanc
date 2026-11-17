"""Durable background job management and scheduling service."""

import json
import random
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerpilot.jobs.models import BackgroundJob


def calculate_backoff(
    attempts: int,
    base: float = 2.0,
    max_backoff: float = 3600.0,
    jitter: float | None = None,
) -> float:
    """Calculate exponential backoff with jitter: base * 2^(attempts - 1) + jitter."""
    exp = base * (2.0 ** max(0, attempts - 1))
    capped = min(max_backoff, exp)
    added_jitter = jitter if jitter is not None else random.uniform(0.1, min(base, 2.0))
    return round(capped + added_jitter, 2)


def enqueue(
    db: Session,
    business_id: UUID | None,
    created_by: UUID | None,
    kind: str,
    payload: dict[str, Any],
    *,
    queue: str = "default",
    idempotency_key: str | None = None,
    max_attempts: int = 5,
    timeout_seconds: int = 300,
    backoff_base_seconds: float = 2.0,
) -> BackgroundJob:
    """Enqueue a durable background job. Deduplicates by idempotency key per business/kind."""
    if idempotency_key:
        query = select(BackgroundJob).where(
            BackgroundJob.kind == kind,
            BackgroundJob.idempotency_key == idempotency_key,
        )
        if business_id is not None:
            query = query.where(BackgroundJob.business_id == business_id)
        else:
            query = query.where(BackgroundJob.business_id.is_(None))
        existing = db.scalar(query)
        if existing is not None:
            return existing

    job = BackgroundJob(
        id=uuid4(),
        business_id=business_id,
        created_by=created_by,
        queue=queue,
        kind=kind,
        idempotency_key=idempotency_key,
        payload=payload,
        state="queued",
        attempts=0,
        max_attempts=max_attempts,
        backoff_base_seconds=Decimal(str(backoff_base_seconds)),
        timeout_seconds=timeout_seconds,
    )
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, business_id: UUID, job_id: UUID) -> BackgroundJob | None:
    """Retrieve job by ID within the authorized business."""
    return db.scalar(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.business_id == business_id,
        )
    )


def claim_job(
    db: Session,
    queue: str,
    worker_token: UUID,
    *,
    lease_seconds: int = 60,
    concurrency_limit: int = 4,
) -> dict[str, Any] | None:
    """Claim next available job using atomic security definer function."""
    row = (
        db.execute(
            text("""
        SELECT id, business_id, created_by, queue, kind, payload,
               attempts, max_attempts, timeout_seconds
        FROM app.claim_background_job(:q, :token, :lease, :limit)
    """),
            {
                "q": queue,
                "token": worker_token,
                "lease": lease_seconds,
                "limit": concurrency_limit,
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return dict(row)


def finish_job(
    db: Session,
    job_id: UUID,
    worker_token: UUID,
    result: dict[str, Any] | None = None,
) -> bool:
    """Mark job as succeeded and record result."""
    return bool(
        db.execute(
            text("SELECT app.finish_background_job(:id, :token, CAST(:res AS jsonb))"),
            {
                "id": job_id,
                "token": worker_token,
                "res": json.dumps(result or {}),
            },
        ).scalar()
    )


def fail_job(
    db: Session,
    job_id: UUID,
    worker_token: UUID,
    error_code: str,
    error_message: str,
    *,
    error_detail: dict[str, Any] | None = None,
    attempts: int = 1,
    backoff_base: float = 2.0,
) -> str:
    """Record job failure. Transitions to queued with backoff, or dead_letter on max attempts."""
    delay = calculate_backoff(attempts, base=backoff_base)
    err = {
        "code": error_code,
        "message": error_message,
        "detail": error_detail or {},
        "failed_at": datetime.now().isoformat(),
    }
    outcome = db.execute(
        text("SELECT app.fail_background_job(:id, :token, CAST(:err AS jsonb), :delay)"),
        {
            "id": job_id,
            "token": worker_token,
            "err": json.dumps(err),
            "delay": delay,
        },
    ).scalar()
    return str(outcome)


def replay_job(db: Session, job_id: UUID) -> bool:
    """Reset a failed or dead_letter job back to queued."""
    return bool(
        db.execute(
            text("SELECT app.replay_background_job(:id)"),
            {"id": job_id},
        ).scalar()
    )


def list_jobs(
    db: Session,
    *,
    queue: str | None = None,
    state: str | None = None,
    business_id: UUID | None = None,
    limit: int = 50,
) -> list[BackgroundJob]:
    """List jobs for inspection or admin operations."""
    stmt = select(BackgroundJob).order_by(BackgroundJob.created_at.desc()).limit(limit)
    if queue:
        stmt = stmt.where(BackgroundJob.queue == queue)
    if state:
        stmt = stmt.where(BackgroundJob.state == state)
    if business_id:
        stmt = stmt.where(BackgroundJob.business_id == business_id)
    return list(db.scalars(stmt))
