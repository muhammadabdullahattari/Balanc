"""Bounded bodies, request correlation, CSRF and distributed auth throttling."""

import json
import logging
import secrets
import time
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ledgerpilot.identity.dependencies import CSRF_COOKIE
from ledgerpilot.identity.security import digest

logger = logging.getLogger("ledgerpilot.requests")


class FoundationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            request_id = str(UUID(request.headers.get("x-request-id", "")))
        except ValueError:
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        settings = scope["app"].state.settings
        started = time.monotonic()

        async def reject(code: str, message: str, status: int, retry_after: int = 0) -> None:
            response = JSONResponse(
                {"error": {"code": code, "message": message}, "request_id": request_id},
                status_code=status,
                headers={
                    "X-Request-ID": request_id,
                    "Cache-Control": "no-store",
                    **({"Retry-After": str(retry_after)} if retry_after else {}),
                },
            )
            await response(scope, receive, send)

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            cookie = request.cookies.get(CSRF_COOKIE, "")
            csrf = request.headers.get("x-csrf-token", "")
            if (
                request.headers.get("origin") not in settings.allowed_origins
                or not cookie
                or not cookie.isascii()
                or not csrf.isascii()
                or not secrets.compare_digest(cookie, csrf)
            ):
                await reject("ERR_CSRF", "Origin or CSRF check failed.", 403)
                return
        chunks: list[bytes] = []
        size = 0
        upload = request.method == "POST" and scope["path"].endswith("/evidence/upload")
        maximum = settings.evidence_max_bytes if upload else 262144
        if upload:
            from ledgerpilot.identity.dependencies import upload_preflight
            from ledgerpilot.shared.errors import DomainError

            try:
                parts = scope["path"].removeprefix(settings.api_v1_prefix).split("/")
                if (
                    len(parts) != 5
                    or parts[1] != "businesses"
                    or parts[3:] != ["evidence", "upload"]
                ):
                    await reject("ERR_NOT_FOUND", "Record not found.", 404)
                    return
                business_id = UUID(parts[2])
            except ValueError:
                await reject("ERR_NOT_FOUND", "Record not found.", 404)
                return
            try:
                await run_in_threadpool(upload_preflight, request, business_id)
            except DomainError as exc:
                await reject(exc.code, exc.message, exc.status, exc.retry_after or 0)
                return
            except SQLAlchemyError:
                await reject("ERR_DEPENDENCY", "Upload authorization is unavailable.", 503)
                return
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > maximum:
                await reject("ERR_REQUEST_SIZE", "Request exceeds the configured size limit.", 413)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        if not upload and excessive_depth(body):
            await reject("ERR_JSON_DEPTH", "JSON nesting exceeds ten levels.", 422)
            return
        if scope["path"].startswith(settings.api_v1_prefix + "/auth/") and request.method == "POST":
            # Counters commit independently so failed requests cannot roll them back.
            ip = request.client.host if request.client else "unknown"
            keys = [(digest("ip:" + ip), 60)]
            try:
                data = json.loads(body)
                if isinstance(data, dict) and isinstance(data.get("email"), str):
                    keys.append((digest("identity:" + data["email"].strip().casefold()), 5))
            except (ValueError, TypeError, RecursionError):
                pass
            limited = 0
            try:
                limited = await run_in_threadpool(
                    rate_limited, scope["app"].state.rate_sessions, keys
                )
            except Exception:
                await reject("ERR_DEPENDENCY", "Authentication is temporarily unavailable.", 503)
                return
            if limited:
                await reject(
                    "ERR_RATE_LIMIT", "Too many attempts. Try again shortly.", 429, limited
                )
                return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def correlated(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
                message["headers"] = headers
                logger.info(
                    json.dumps(
                        {
                            "event": "request",
                            "request_id": request_id,
                            "method": request.method,
                            "status": message["status"],
                            "duration_ms": round((time.monotonic() - started) * 1000, 2),
                        }
                    )
                )
            await send(message)

        await self.app(scope, replay, correlated)


def rate_limited(factory: sessionmaker[Session], keys: list[tuple[str, int]]) -> int:
    delay = 0
    with factory.begin() as db:
        for key, limit in sorted(keys):
            row = (
                db.execute(
                    text("""
                INSERT INTO app.auth_rate_buckets(key,started_at,attempts,blocked_until)
                VALUES(:key,now(),1,NULL) ON CONFLICT(key) DO UPDATE SET
                attempts=CASE WHEN app.auth_rate_buckets.started_at < now()-interval '1 minute'
                  THEN 1 ELSE least(app.auth_rate_buckets.attempts+1,1000) END,
                started_at=CASE WHEN app.auth_rate_buckets.started_at < now()-interval '1 minute'
                  THEN now() ELSE app.auth_rate_buckets.started_at END,
                blocked_until=CASE
                  WHEN app.auth_rate_buckets.started_at < now()-interval '1 minute' THEN NULL
                  WHEN app.auth_rate_buckets.attempts>=:budget THEN greatest(
                    coalesce(app.auth_rate_buckets.blocked_until,now()),now()+
                    least(power(2,least(app.auth_rate_buckets.attempts-:budget,6)),60)::int
                    * interval '1 second')
                  ELSE app.auth_rate_buckets.blocked_until END
                RETURNING attempts, ceil(extract(epoch FROM
                    greatest(blocked_until,started_at+interval '1 minute')-now())) AS wait
            """),
                    {"key": key, "budget": limit},
                )
                .mappings()
                .one()
            )
            if row["attempts"] > limit:
                delay = max(delay, max(1, int(row["wait"])))
    return delay


def excessive_depth(body: bytes) -> bool:
    depth = 0
    quoted = escaped = False
    for byte in body:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 10:
                return True
        elif byte in (93, 125):
            depth -= 1
    return False
