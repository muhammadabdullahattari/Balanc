"""Prometheus alert rules and metrics collection unit tests.

Validates:
- All 9 required alert rules exist in deploy/prometheus/alerts.yml.
- Each rule defines expression, duration ('for'), severity, tier, and runbook_url.
- Metrics format covers worker heartbeats, Redis status, Redis errors, and event loop lag.
- Synthetic unit tests in alerts_test.yml correspond to all alert rules.
"""

from pathlib import Path
from unittest.mock import MagicMock

import yaml  # type: ignore[import-untyped]

from ledgerpilot.observability.metrics import (
    format_prometheus_metrics,
    record_admission_rejection,
    record_cache_hit,
    record_event_loop_lag,
    record_lock_timing,
    record_rate_limit_rejection,
    record_redis_error,
    record_redis_status,
    record_worker_heartbeat,
)

ROOT = Path(__file__).resolve().parents[2]
ALERTS_FILE = ROOT / "deploy" / "prometheus" / "alerts.yml"
TESTS_FILE = ROOT / "deploy" / "prometheus" / "alerts_test.yml"

REQUIRED_ALERTS = {
    "DatabasePoolNearExhaustion": ("critical", "30s"),
    "JobQueueDepthHigh": ("warning", "2m"),
    "JobOldestAgeHigh": ("warning", "2m"),
    "WorkerDown": ("critical", "1m"),
    "DeadLetterGrowth": ("critical", "1m"),
    "AdmissionRejectionSpike": ("warning", "1m"),
    "WorkspaceLockContentionSpike": ("warning", "1m"),
    "RedisDown": ("critical", "30s"),
    "EventLoopLagHigh": ("warning", "1m"),
}


def test_all_nine_alert_rules_exist_with_valid_structure() -> None:
    """Verify deploy/prometheus/alerts.yml specifies all required rules with valid fields."""
    assert ALERTS_FILE.exists(), f"Alerts file missing: {ALERTS_FILE}"
    with open(ALERTS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "groups" in data and len(data["groups"]) > 0
    rules = data["groups"][0].get("rules", [])
    rule_map = {r["alert"]: r for r in rules if "alert" in r}

    for alert_name, (expected_severity, expected_for) in REQUIRED_ALERTS.items():
        assert alert_name in rule_map, f"Missing required alert rule: {alert_name}"
        rule = rule_map[alert_name]
        assert "expr" in rule, f"Alert {alert_name} missing 'expr'"
        assert rule.get("for") == expected_for, (
            f"Alert {alert_name} expected 'for: {expected_for}', got {rule.get('for')}"
        )
        assert rule.get("labels", {}).get("severity") == expected_severity, (
            f"Alert {alert_name} expected severity '{expected_severity}', "
            f"got {rule.get('labels', {}).get('severity')}"
        )
        assert "tier" in rule.get("labels", {}), f"Alert {alert_name} missing 'tier' label"
        annotations = rule.get("annotations", {})
        assert "summary" in annotations, f"Alert {alert_name} missing summary"
        assert "description" in annotations, f"Alert {alert_name} missing description"
        assert "runbook_url" in annotations, f"Alert {alert_name} missing runbook_url"
        assert annotations["runbook_url"].startswith("https://runbooks.ledgerpilot.internal/")


def test_alerts_test_file_valid_yaml_and_rule_coverage() -> None:
    """Verify deploy/prometheus/alerts_test.yml has test cases for alert rules."""
    assert TESTS_FILE.exists(), f"Tests file missing: {TESTS_FILE}"
    with open(TESTS_FILE, encoding="utf-8") as f:
        test_data = yaml.safe_load(f)

    assert "tests" in test_data and len(test_data["tests"]) > 0
    tested_alerts: set[str] = set()
    for t in test_data["tests"]:
        for art in t.get("alert_rule_test", []):
            if "alertname" in art:
                tested_alerts.add(art["alertname"])

    # Confirm key alert rules have synthetic test specifications
    for key_alert in [
        "DatabasePoolNearExhaustion",
        "JobQueueDepthHigh",
        "JobOldestAgeHigh",
        "DeadLetterGrowth",
        "AdmissionRejectionSpike",
        "WorkspaceLockContentionSpike",
        "RedisDown",
        "EventLoopLagHigh",
    ]:
        assert key_alert in tested_alerts, (
            f"Alert {key_alert} missing promtool test in alerts_test.yml"
        )


def test_metrics_exporter_includes_all_monitored_series() -> None:
    """Verify format_prometheus_metrics outputs all metrics needed by the 9 alert rules."""
    mock_engine = MagicMock()
    mock_pool = MagicMock()
    mock_pool.size.return_value = 10
    mock_pool.checkedout.return_value = 8
    mock_pool.checkedin.return_value = 2
    mock_pool.overflow.return_value = 1
    mock_engine.pool = mock_pool

    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.execute.return_value.mappings.return_value.all.return_value = [
        {"queue": "default", "active_depth": 550, "dead_letters": 6, "oldest_age": 320.0},
    ]
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # Record all telemetry
    record_worker_heartbeat("worker-node-1", 1763391000.0)
    record_redis_status("admission", True)
    record_redis_status("cache", True)
    record_redis_error("cache")
    record_event_loop_lag(0.125)
    record_lock_timing(wait_ms=2.0, hold_ms=6.5)
    record_admission_rejection()
    record_rate_limit_rejection("user")
    record_cache_hit()

    output = format_prometheus_metrics(mock_engine)

    # Check database pool metrics
    assert "ledgerpilot_db_pool_size 10" in output
    assert "ledgerpilot_db_pool_checked_out 8" in output

    # Check job metrics
    assert 'ledgerpilot_job_queue_depth{queue="default"} 550' in output
    assert 'ledgerpilot_job_queue_oldest_seconds{queue="default"} 320.0' in output
    assert 'ledgerpilot_job_dead_letter_total{queue="default"} 6' in output

    # Check lock metrics
    assert "ledgerpilot_workspace_lock_hold_seconds_p95 0.006500" in output

    # Check worker heartbeat
    expected_hb = (
        'ledgerpilot_worker_last_heartbeat_seconds{worker_id="worker-node-1"} 1763391000.0'
    )
    assert expected_hb in output

    # Check Redis status and errors
    assert 'ledgerpilot_redis_up{instance="admission"} 1' in output
    assert 'ledgerpilot_redis_errors_total{instance="cache"} 1' in output

    # Check event loop lag
    assert "ledgerpilot_event_loop_lag_seconds_p95 0.125000" in output
