"""Allowlisted operational JSON; never serialize exceptions, SQL or request bodies."""

import json
import logging
import re
import traceback
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

_context: ContextVar[dict[str, Any] | None] = ContextVar("request_log_context", default=None)


@contextmanager
def request_logging(state: dict[str, Any]) -> Iterator[None]:
    token = _context.set(state)
    try:
        yield
    finally:
        _context.reset(token)


def bind_identity(user_id: UUID, business_id: UUID | None = None) -> None:
    state = _context.get()
    # Only mutate a context installed by this request, never the default dict.
    if state:
        state["user_id"] = str(user_id)
        if business_id is not None:
            state["business_id"] = str(business_id)


def emit(
    event: str, *, level: int = logging.INFO, error: Exception | None = None, **fields: Any
) -> None:
    payload: dict[str, Any] = {"event": event}
    for key in ("request_id", "user_id", "business_id", "resource_id", "job_id"):
        value = fields.get(key, (_context.get() or {}).get(key))
        if value is not None:
            with suppress(ValueError):
                payload[key] = str(UUID(str(value)))
    for key in ("action", "code", "stage", "sqlstate"):
        value = fields.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
            payload[key] = value
    for key in ("status", "dropped_key_count", "count", "window_seconds", "duration_ms"):
        if isinstance(fields.get(key), int):
            payload[key] = fields[key]
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["frames"] = [
            {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)[-12:]
        ]
    logging.getLogger("ledgerpilot.operations").log(level, json.dumps(payload))
