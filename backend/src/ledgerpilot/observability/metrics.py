"""Prometheus-compatible metrics collection and text exposition for SRE visibility."""

import threading
import time

from sqlalchemy import text
from sqlalchemy.engine import Engine

_lock = threading.Lock()

# Thread-safe in-memory counters and gauges
_http_requests_total: dict[str, int] = {}
_admission_rejections_total: int = 0
_rate_limit_rejections_total: dict[str, int] = {"user": 0, "workspace": 0, "ip": 0}
_report_cache_hits: int = 0
_report_cache_misses: int = 0
_report_cache_evictions: int = 0
_workspace_lock_wait_samples: list[float] = []
_workspace_lock_hold_samples: list[float] = []

# SRE additions for Phase 0 alerts
_worker_heartbeats: dict[str, float] = {}
_redis_up: dict[str, int] = {"admission": 1, "cache": 1}
_redis_errors_total: dict[str, int] = {"admission": 0, "cache": 0}
_event_loop_lag_samples: list[float] = []


def record_http_request(method: str, status: int) -> None:
    key = f"{method}_{status}"
    with _lock:
        _http_requests_total[key] = _http_requests_total.get(key, 0) + 1


def record_admission_rejection() -> None:
    global _admission_rejections_total
    with _lock:
        _admission_rejections_total += 1


def record_rate_limit_rejection(tier: str) -> None:
    with _lock:
        _rate_limit_rejections_total[tier] = _rate_limit_rejections_total.get(tier, 0) + 1


def record_cache_hit() -> None:
    global _report_cache_hits
    with _lock:
        _report_cache_hits += 1


def record_cache_miss() -> None:
    global _report_cache_misses
    with _lock:
        _report_cache_misses += 1


def record_cache_eviction() -> None:
    global _report_cache_evictions
    with _lock:
        _report_cache_evictions += 1


def record_lock_timing(wait_ms: float, hold_ms: float) -> None:
    with _lock:
        _workspace_lock_wait_samples.append(wait_ms)
        _workspace_lock_hold_samples.append(hold_ms)
        if len(_workspace_lock_wait_samples) > 1000:
            del _workspace_lock_wait_samples[:500]
            del _workspace_lock_hold_samples[:500]


def record_worker_heartbeat(worker_id: str, timestamp: float | None = None) -> None:
    """Record heartbeat timestamp for background worker monitoring."""
    with _lock:
        _worker_heartbeats[worker_id] = timestamp or time.time()


def record_redis_status(instance: str, is_up: bool) -> None:
    """Record reachability status for Redis instance (admission or cache)."""
    with _lock:
        _redis_up[instance] = 1 if is_up else 0


def record_redis_error(instance: str) -> None:
    """Increment operational error count for Redis instance."""
    with _lock:
        _redis_errors_total[instance] = _redis_errors_total.get(instance, 0) + 1


def record_event_loop_lag(lag_seconds: float) -> None:
    """Record event loop lag measurement in seconds."""
    with _lock:
        _event_loop_lag_samples.append(lag_seconds)
        if len(_event_loop_lag_samples) > 1000:
            del _event_loop_lag_samples[:500]


def format_prometheus_metrics(engine: Engine | None = None) -> str:
    """Format all metrics in Prometheus text exposition format (version 0.0.4)."""
    lines: list[str] = []

    def add_comment(name: str, metric_type: str, help_text: str) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")

    # 1. Database connection pool metrics
    if engine is not None and hasattr(engine, "pool"):
        pool = engine.pool
        add_comment("ledgerpilot_db_pool_size", "gauge", "Configured database connection pool size")
        lines.append(f"ledgerpilot_db_pool_size {getattr(pool, 'size', lambda: 5)()}")

        add_comment(
            "ledgerpilot_db_pool_checked_out",
            "gauge",
            "Number of database connections currently in use",
        )
        lines.append(f"ledgerpilot_db_pool_checked_out {getattr(pool, 'checkedout', lambda: 0)()}")

        add_comment(
            "ledgerpilot_db_pool_checked_in",
            "gauge",
            "Number of idle database connections available in the pool",
        )
        lines.append(f"ledgerpilot_db_pool_checked_in {getattr(pool, 'checkedin', lambda: 0)()}")

        add_comment(
            "ledgerpilot_db_pool_overflow",
            "gauge",
            "Number of active overflow connections above base pool size",
        )
        lines.append(f"ledgerpilot_db_pool_overflow {getattr(pool, 'overflow', lambda: 0)()}")

    # 2. Background job queue metrics
    if engine is not None:
        try:
            with engine.connect() as conn:
                if conn.dialect.name == "postgresql":
                    job_stats = (
                        conn.execute(
                            text("""
                        SELECT
                            queue,
                            count(*) FILTER (WHERE state IN ('queued', 'running')) AS active_depth,
                            count(*) FILTER (WHERE state = 'dead_letter') AS dead_letters,
                            coalesce(
                                extract(epoch FROM (now() - min(created_at))),
                                0
                            ) AS oldest_age
                        FROM app.background_jobs
                        GROUP BY queue
                    """)
                        )
                        .mappings()
                        .all()
                    )

                    add_comment(
                        "ledgerpilot_job_queue_depth",
                        "gauge",
                        "Number of active jobs queued or running per queue",
                    )
                    for row in job_stats:
                        q_name = row["queue"]
                        val = row["active_depth"]
                        lines.append(f'ledgerpilot_job_queue_depth{{queue="{q_name}"}} {val}')

                    add_comment(
                        "ledgerpilot_job_queue_oldest_seconds",
                        "gauge",
                        "Age in seconds of the oldest unprocessed queued job",
                    )
                    for row in job_stats:
                        q_name = row["queue"]
                        age = float(row["oldest_age"])
                        lines.append(
                            f'ledgerpilot_job_queue_oldest_seconds{{queue="{q_name}"}} {age:.1f}'
                        )

                    add_comment(
                        "ledgerpilot_job_dead_letter_total",
                        "counter",
                        "Total count of dead-lettered background jobs",
                    )
                    for row in job_stats:
                        q_name = row["queue"]
                        dl = row["dead_letters"]
                        lines.append(f'ledgerpilot_job_dead_letter_total{{queue="{q_name}"}} {dl}')
        except Exception:
            pass

    # 3. Workspace contention metrics
    with _lock:
        wait_samples = list(_workspace_lock_wait_samples)
        hold_samples = list(_workspace_lock_hold_samples)

    p95_wait = 0.0
    p95_hold = 0.0
    if wait_samples:
        wait_sorted = sorted(wait_samples)
        p95_wait = wait_sorted[int(len(wait_sorted) * 0.95)] / 1000.0
    if hold_samples:
        hold_sorted = sorted(hold_samples)
        p95_hold = hold_sorted[int(len(hold_sorted) * 0.95)] / 1000.0

    add_comment(
        "ledgerpilot_workspace_lock_wait_seconds_p95",
        "gauge",
        "95th percentile workspace row lock wait duration",
    )
    lines.append(f"ledgerpilot_workspace_lock_wait_seconds_p95 {p95_wait:.6f}")

    add_comment(
        "ledgerpilot_workspace_lock_hold_seconds_p95",
        "gauge",
        "95th percentile workspace row lock hold duration (<0.005s target)",
    )
    lines.append(f"ledgerpilot_workspace_lock_hold_seconds_p95 {p95_hold:.6f}")

    # 4. Reports cache metrics
    with _lock:
        hits = _report_cache_hits
        misses = _report_cache_misses
        evictions = _report_cache_evictions
        admission_rejections = _admission_rejections_total
        tier_rejections = dict(_rate_limit_rejections_total)

    total_cache = hits + misses
    hit_ratio = (hits / total_cache) if total_cache > 0 else 0.0

    add_comment("ledgerpilot_report_cache_hits_total", "counter", "Total reports cache hits")
    lines.append(f"ledgerpilot_report_cache_hits_total {hits}")

    add_comment("ledgerpilot_report_cache_misses_total", "counter", "Total reports cache misses")
    lines.append(f"ledgerpilot_report_cache_misses_total {misses}")

    add_comment("ledgerpilot_report_cache_hit_ratio", "gauge", "Reports cache hit ratio")
    lines.append(f"ledgerpilot_report_cache_hit_ratio {hit_ratio:.4f}")

    add_comment("ledgerpilot_report_cache_evictions_total", "counter", "Reports cache evictions")
    lines.append(f"ledgerpilot_report_cache_evictions_total {evictions}")

    # 5. Admission and rate limit rejection metrics
    add_comment(
        "ledgerpilot_admission_rejections_total",
        "counter",
        "Total requests rejected by Redis admission control (HTTP 503)",
    )
    lines.append(f"ledgerpilot_admission_rejections_total {admission_rejections}")

    add_comment(
        "ledgerpilot_rate_limit_rejections_total",
        "counter",
        "Total requests throttled by rate limit tier (HTTP 429)",
    )
    for tier, count in tier_rejections.items():
        lines.append(f'ledgerpilot_rate_limit_rejections_total{{tier="{tier}"}} {count}')

    # 6. Background worker heartbeat metrics
    with _lock:
        heartbeats = dict(_worker_heartbeats)
        redis_states = dict(_redis_up)
        redis_errs = dict(_redis_errors_total)
        lag_samples = list(_event_loop_lag_samples)

    if heartbeats:
        add_comment(
            "ledgerpilot_worker_last_heartbeat_seconds",
            "gauge",
            "Timestamp of most recent background worker heartbeat",
        )
        for w_id, ts in heartbeats.items():
            lines.append(
                f'ledgerpilot_worker_last_heartbeat_seconds{{worker_id="{w_id}"}} {ts:.1f}'
            )

    # 7. Redis status and operational error metrics
    add_comment(
        "ledgerpilot_redis_up",
        "gauge",
        "Redis instance reachability status (1=up, 0=down)",
    )
    for inst, state in redis_states.items():
        lines.append(f'ledgerpilot_redis_up{{instance="{inst}"}} {state}')

    add_comment(
        "ledgerpilot_redis_errors_total",
        "counter",
        "Total operational errors encountered communicating with Redis",
    )
    for inst, err_count in redis_errs.items():
        lines.append(f'ledgerpilot_redis_errors_total{{instance="{inst}"}} {err_count}')

    # 8. Event loop lag metrics
    p95_lag = 0.0
    if lag_samples:
        lag_sorted = sorted(lag_samples)
        p95_lag = lag_sorted[int(len(lag_sorted) * 0.95)]

    add_comment(
        "ledgerpilot_event_loop_lag_seconds_p95",
        "gauge",
        "95th percentile FastAPI event loop lag in seconds (<0.1s target)",
    )
    lines.append(f"ledgerpilot_event_loop_lag_seconds_p95 {p95_lag:.6f}")

    lines.append("")  # Trailing newline required by Prometheus
    return "\n".join(lines)
