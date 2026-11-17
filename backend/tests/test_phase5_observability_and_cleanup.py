"""Phase 5 Observability and Cleanup verification.

Covers:
- M4: Prometheus-compatible metrics endpoint exposition and alert rules file validation.
- L1: Fallback rate-limit counters pruning, table boundedness under load, active block preservation.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ledgerpilot.database import Base
from ledgerpilot.identity.models import AuthRateBucket
from ledgerpilot.observability.janitor import prune_expired_rate_limit_buckets
from ledgerpilot.observability.metrics import (
    format_prometheus_metrics,
    record_admission_rejection,
    record_cache_hit,
    record_cache_miss,
    record_lock_timing,
    record_rate_limit_rejection,
)


def test_m4_prometheus_metrics_format_and_collectors() -> None:
    """Verify Prometheus text format covers database pools, job queues, lock timings,
    cache metrics, and rate limit tiers.
    """
    mock_engine = MagicMock()
    mock_pool = MagicMock()
    mock_pool.size.return_value = 10
    mock_pool.checkedout.return_value = 4
    mock_pool.checkedin.return_value = 6
    mock_pool.overflow.return_value = 0
    mock_engine.pool = mock_pool

    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.execute.return_value.mappings.return_value.all.return_value = [
        {"queue": "default", "active_depth": 12, "dead_letters": 1, "oldest_age": 4.5},
        {"queue": "webhook", "active_depth": 0, "dead_letters": 0, "oldest_age": 0.0},
    ]
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # Record some sample telemetry
    record_cache_hit()
    record_cache_hit()
    record_cache_miss()
    record_admission_rejection()
    record_rate_limit_rejection("user")
    record_rate_limit_rejection("workspace")
    record_lock_timing(wait_ms=1.2, hold_ms=0.8)

    output = format_prometheus_metrics(mock_engine)

    # 1. Database pool
    assert "ledgerpilot_db_pool_size 10" in output
    assert "ledgerpilot_db_pool_checked_out 4" in output
    assert "ledgerpilot_db_pool_checked_in 6" in output

    # 2. Background jobs
    assert 'ledgerpilot_job_queue_depth{queue="default"} 12' in output
    assert 'ledgerpilot_job_queue_oldest_seconds{queue="default"} 4.5' in output
    assert 'ledgerpilot_job_dead_letter_total{queue="default"} 1' in output

    # 3. Workspace lock
    assert "ledgerpilot_workspace_lock_hold_seconds_p95" in output

    # 4. Reports cache
    assert "ledgerpilot_report_cache_hits_total" in output
    assert "ledgerpilot_report_cache_hit_ratio" in output

    # 5. Rate limit and admission rejections
    assert "ledgerpilot_admission_rejections_total" in output
    assert 'ledgerpilot_rate_limit_rejections_total{tier="user"}' in output
    assert 'ledgerpilot_rate_limit_rejections_total{tier="workspace"}' in output


def test_m4_alert_rules_file_validity() -> None:
    """Verify deploy/prometheus/alerts.yml syntax and coverage of the required alerts:
    pool exhaustion (>80% for >30s), queue backlog growth, worker crash loop, and admission spike.
    """
    repo_root = Path(__file__).resolve().parents[2]
    alerts_file = repo_root / "deploy" / "prometheus" / "alerts.yml"
    assert alerts_file.exists(), f"Alerts file missing at {alerts_file}"

    with alerts_file.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "groups" in data
    group = data["groups"][0]
    rules = {rule["alert"]: rule for rule in group["rules"]}

    # Required alerts
    assert "DatabasePoolNearExhaustion" in rules
    assert "JobQueueBacklogGrowth" in rules
    assert "WorkerCrashLoop" in rules
    assert "AdmissionRejectionSpike" in rules
    assert "WorkspaceLockContentionSpike" in rules

    # Check expression for pool exhaustion
    pool_rule = rules["DatabasePoolNearExhaustion"]
    assert "0.80" in pool_rule["expr"] or "0.8" in pool_rule["expr"]
    assert pool_rule["for"] == "30s"


def test_l1_fallback_rate_limit_counter_janitor_bounds_table() -> None:
    """Verify that the background janitor prunes expired rate-limit buckets from PostgreSQL
    storage under sustained load, preventing table leakage while preserving active blocks.
    """
    engine = create_engine("sqlite:///:memory:")
    # Remove schema from table args for sqlite in-memory testing
    AuthRateBucket.__table__.schema = None
    Base.metadata.create_all(engine, tables=[AuthRateBucket.__table__])
    session_factory = sessionmaker(engine, expire_on_commit=False)

    now_ts = datetime.now(UTC)
    expired_ts = now_ts - timedelta(hours=2)
    active_ts = now_ts - timedelta(seconds=10)
    future_block = now_ts + timedelta(minutes=15)

    with session_factory() as db:
        # Simulate 1,000 expired requests during a simulated Redis outage
        expired_buckets = [
            AuthRateBucket(
                key=f"user_{i}_{uuid4().hex[:8]}",
                started_at=expired_ts,
                attempts=5,
                blocked_until=None,
            )
            for i in range(1000)
        ]
        db.add_all(expired_buckets)

        # Add 5 active buckets (within 1 hour window)
        active_buckets = [
            AuthRateBucket(
                key=f"active_{i}",
                started_at=active_ts,
                attempts=2,
                blocked_until=None,
            )
            for i in range(5)
        ]
        db.add_all(active_buckets)

        # Add 3 blocked buckets (expired started_at but active block in the future)
        blocked_buckets = [
            AuthRateBucket(
                key=f"blocked_{i}",
                started_at=expired_ts,
                attempts=20,
                blocked_until=future_block,
            )
            for i in range(3)
        ]
        db.add_all(blocked_buckets)
        db.commit()

        # Pre-cleanup count: 1000 + 5 + 3 = 1008
        pre_count = len(list(db.scalars(select(AuthRateBucket))))
        assert pre_count == 1008

        # Run the background janitor with a 3600-second (1 hour) window
        pruned = prune_expired_rate_limit_buckets(db, window_seconds=3600)
        db.commit()

        assert pruned == 1000

        # Post-cleanup: only 5 active + 3 blocked remain (8 total)
        remaining = list(db.scalars(select(AuthRateBucket)))
        assert len(remaining) == 8

        # Verify active buckets preserved
        active_keys = {b.key for b in remaining}
        for i in range(5):
            assert f"active_{i}" in active_keys

        # Verify blocked buckets preserved
        for i in range(3):
            assert f"blocked_{i}" in active_keys
