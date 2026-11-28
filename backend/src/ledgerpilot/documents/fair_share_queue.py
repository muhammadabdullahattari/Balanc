"""G1: Fair-share live vs. bulk ingestion queue isolating live traffic from 500k-page backlogs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

LaneKind = Literal["live", "bulk"]


def enqueue_ingestion_job(
    db: Session,
    *,
    business_id: UUID,
    lane: LaneKind,
    page_count: int = 1,
    batch_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> UUID:
    """Enqueue an ingestion job in either the isolated 'live' lane or fair-share 'bulk' lane."""
    jid = uuid4()
    meta = payload or {}
    fname = str(meta.get("batch_reference") or meta.get("filename") or "batch")
    db.execute(
        text(
            """
            INSERT INTO app.ingestion_queue_jobs (
              id, business_id, batch_id, lane, filename, page_count, state
            ) VALUES (
              :id, :bid, :batch_id, :lane, :fname, :pages, 'queued'
            )
            """
        ),
        {
            "id": jid,
            "bid": business_id,
            "batch_id": batch_id,
            "lane": lane,
            "fname": fname[:200],
            "pages": max(1, int(page_count)),
        },
    )
    return jid


def lease_next_fair_share_job(
    db: Session,
    *,
    business_id: UUID,
    prefer_lane: LaneKind = "live",
    max_bulk_inflight_per_tenant: int = 8,
    lease_seconds: int = 60,
) -> dict[str, Any] | None:
    """Lease the next queued job using FOR UPDATE SKIP LOCKED while prioritizing live over bulk."""
    lanes = ["live", "bulk"] if prefer_lane == "live" else ["bulk", "live"]
    lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)

    for candidate_lane in lanes:
        if candidate_lane == "bulk":
            inflight = int(
                db.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM app.ingestion_queue_jobs
                        WHERE business_id = :bid
                          AND lane = 'bulk'
                          AND state = 'leased'
                          AND lease_until > now()
                        """
                    ),
                    {"bid": business_id},
                )
                or 0
            )
            if inflight >= max_bulk_inflight_per_tenant:
                continue

        row = (
            db.execute(
                text(
                    """
                    UPDATE app.ingestion_queue_jobs
                    SET state = 'leased',
                        lease_until = :exp
                    WHERE id = (
                      SELECT id
                      FROM app.ingestion_queue_jobs
                      WHERE business_id = :bid
                        AND lane = :lane
                        AND (
                          state = 'queued'
                          OR (state = 'leased' AND lease_until <= now())
                        )
                      ORDER BY created_at ASC
                      LIMIT 1
                      FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, business_id, batch_id, lane, page_count
                    """
                ),
                {"exp": lease_until, "bid": business_id, "lane": candidate_lane},
            )
            .mappings()
            .first()
        )
        if row is not None:
            return {
                "id": str(row["id"]),
                "business_id": str(row["business_id"]),
                "batch_id": str(row["batch_id"]) if row["batch_id"] else None,
                "lane": str(row["lane"]),
                "page_count": int(row["page_count"]),
                "attempts": 1,
            }
    return None


def complete_leased_job(db: Session, *, business_id: UUID, job_id: UUID) -> None:
    """Mark a leased ingestion queue job completed."""
    db.execute(
        text(
            """
            UPDATE app.ingestion_queue_jobs
            SET state = 'completed', completed_at = now(), lease_until = NULL
            WHERE business_id = :bid AND id = :id
            """
        ),
        {"bid": business_id, "id": job_id},
    )


def queue_depth_summary(db: Session, business_id: UUID) -> dict[str, int]:
    """Return real queued/leased/completed counts per lane for the tenant."""
    rows = (
        db.execute(
            text(
                """
                SELECT lane, state, coalesce(sum(page_count), 0) AS pages
                FROM app.ingestion_queue_jobs
                WHERE business_id = :bid
                GROUP BY lane, state
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    out = {
        "live_queued_pages": 0,
        "live_completed_pages": 0,
        "bulk_queued_pages": 0,
        "bulk_completed_pages": 0,
    }
    for r in rows:
        key = (
            f"{r['lane']}_{'queued' if r['state'] in {'queued', 'leased'} else 'completed'}_pages"
        )
        if key in out:
            out[key] += int(r["pages"] or 0)
    return out
