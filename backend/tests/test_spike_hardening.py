"""Infrastructure failure and isolation regression tests."""

import os
from unittest.mock import MagicMock

import pytest
from redis.exceptions import ConnectionError

from ledgerpilot.reports.cache import report
from ledgerpilot.shared.admission import RedisAdmission
from ledgerpilot.shared.errors import DomainError


def test_redis_admission_unavailable_fails_closed() -> None:
    admission = RedisAdmission("redis://localhost:6379", 2, 0.1)
    admission._script = MagicMock(side_effect=ConnectionError())
    with pytest.raises(DomainError) as error:
        admission.check([("key", 1)], 60, 60)
    assert error.value.code == "ERR_DEPENDENCY"
    admission.close()


@pytest.mark.skipif(not os.getenv("LEDGERPILOT_TEST_REDIS_URL"), reason="Requires isolated Redis")
def test_redis_atomic_budgets() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    admission = RedisAdmission(os.environ["LEDGERPILOT_TEST_REDIS_URL"], 32, 1)
    key = uuid4().hex
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(lambda _: admission.check([(key, 5)], 2, 2), range(20)))
        assert decisions.count(0) == 5
    finally:
        admission.close()


def test_report_cache_hit_isolation_version_change_and_redis_down() -> None:
    db, redis = MagicMock(), MagicMock()
    db.scalar.return_value = True
    values: dict[str, str] = {}
    redis.get.side_effect = values.get
    redis.set.side_effect = lambda key, value, **kwargs: values.__setitem__(key, value)
    compute = MagicMock(return_value={"report_id": "source", "total": "10"})
    scope = ["dashboard", "tenant-a", "actor-a", "version-1"]
    first = report(db, redis, 10, scope, compute)
    second = report(db, redis, 10, scope, compute)
    assert first["total"] == second["total"] and first["report_id"] != second["report_id"]
    compute.assert_called_once()
    report(db, redis, 10, [*scope[:-1], "version-2"], compute)
    report(db, redis, 10, ["dashboard", "tenant-b", *scope[2:]], compute)
    assert compute.call_count == 3
    redis.get.side_effect = redis.set.side_effect = ConnectionError()
    assert report(db, redis, 10, scope, compute)["total"] == "10"
    db.scalar.return_value = False
    with pytest.raises(DomainError) as error:
        report(db, redis, 10, scope, compute)
    assert error.value.code == "ERR_REPORT_BUSY"
