"""Cache authorized report computations, while keeping report audit IDs unique."""

import hashlib
import json
import random
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import emit


def report(
    db: Session,
    client: Redis | None,
    ttl: int,
    scope: list[str],
    compute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if ttl == 0 or client is None:
        return compute()
    fingerprint = hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).digest()
    key = "ledgerpilot:v1:report:private:" + fingerprint.hex()
    try:
        cached = client.get(key)
        from ledgerpilot.observability.metrics import record_redis_status
        record_redis_status("cache", True)
    except RedisError:
        cached = None
        from ledgerpilot.observability.metrics import record_redis_error, record_redis_status
        record_redis_status("cache", False)
        record_redis_error("cache")
        emit("report_cache.unavailable")
    if isinstance(cached, (bytes, str)):
        emit("report_cache.hit")
        result: dict[str, Any] = json.loads(cached)
        for item in [result, *[v for v in result.values() if isinstance(v, dict)]]:
            if "report_id" in item:
                item["report_id"] = str(uuid4())
        return result

    # Share this lock across replicas, including during a Redis outage.
    # Fail fast instead of pinning database connections behind a popular report.
    lock = int.from_bytes(fingerprint[:8], "big", signed=True)
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}):
        raise DomainError("ERR_REPORT_BUSY", "This report is being prepared. Retry shortly.", 503)
    emit("report_cache.miss")
    result = compute()
    encoded = json.dumps(jsonable_encoder(result), separators=(",", ":"))
    if len(encoded.encode()) <= 512 * 1024:
        try:
            client.set(key, encoded, ex=ttl + random.randint(0, max(1, ttl // 4)))
        except RedisError:
            emit("report_cache.unavailable")
    return result
