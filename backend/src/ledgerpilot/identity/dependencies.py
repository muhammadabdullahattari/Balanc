"""Authenticated identity and per-operation authorization dependencies."""

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from ledgerpilot.identity import repository, service
from ledgerpilot.identity.models import AuthSession, User
from ledgerpilot.identity.security import decode_access, digest
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import bind_identity

ACCESS_COOKIE = "__Host-lp_access"
REFRESH_COOKIE = "__Host-lp_refresh"
CSRF_COOKIE = "__Host-lp_csrf"
SESSION_COOKIE = APIKeyCookie(name=ACCESS_COOKIE, scheme_name="BrowserSession", auto_error=False)


def db_session(request: Request) -> Iterator[Session]:
    # begin() commits only after successful handling and rolls back body, flush
    # and commit failures. Let the central safe error handler see the exception;
    # never continue querying an aborted transaction or return partial success.
    with request.app.state.sessions.begin() as db:
        # Set isolation before authentication establishes the transaction snapshot.
        # Commands retain READ COMMITTED; reports may persist their report artifacts.
        snapshot = (request.method == "GET" and "/businesses/" in request.url.path) or (
            request.method == "POST" and request.url.path.endswith("/exports/snapshots")
        )
        if snapshot:
            db.info["consistent_snapshot"] = True
        db.info["http_method"] = request.method
        db.info["request_path"] = request.url.path
        db.info["rate_factory"] = request.app.state.rate_sessions
        db.info["rate_window_seconds"] = request.app.state.settings.rate_window_seconds
        db.info["rate_cooldown_max_seconds"] = request.app.state.settings.rate_cooldown_max_seconds
        admitted = getattr(request.state, "upload_rate_scope", None)
        if admitted is not None:
            db.info["admitted_scope"] = admitted
        yield db


DB = Annotated[Session, Depends(db_session, scope="function")]


@dataclass
class Principal:
    user: User
    family: AuthSession


def principal(
    request: Request, db: DB, token: Annotated[str | None, Security(SESSION_COOKIE)] = None
) -> Principal:
    uid, sid = decode_access(request.app.state.settings, token or "")
    snapshot = bool(db.info.get("consistent_snapshot"))
    if snapshot:
        # Authenticate and touch activity in a short transaction, before the report
        # establishes its MVCC snapshot. Never hold the session write lock during IO.
        with request.app.state.sessions.begin() as auth_db:
            principal(request, auth_db, token)
        # Acquire the report connection only after releasing the auth connection;
        # otherwise concurrent reads can exhaust the pool while awaiting a second slot.
        db.connection(
            execution_options={
                "isolation_level": "SERIALIZABLE" if request.method == "POST" else "REPEATABLE READ"
            }
        )
    family = repository.session_record(db, uid, sid, lock=not snapshot)
    if family is None:
        raise DomainError("ERR_AUTH", "Authentication required.", 401)
    user = service.valid_family(db, family, touch=not snapshot)
    bind_identity(user.id)
    # Session CSRF binding is checked against this exact request's header.
    # FoundationMiddleware additionally checks its cookie and allowed Origin.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(family.csrf_hash, digest(csrf)):
            raise DomainError("ERR_CSRF", "CSRF check failed.", 403)
    if getattr(request.state, "authenticated_rate_scope", None) != user.id:
        from ledgerpilot.shared.middleware import rate_limited

        delay = rate_limited(
            request.app.state.rate_sessions,
            [(digest(f"user:{user.id}"), 240)],
            window_seconds=request.app.state.settings.rate_window_seconds,
            cooldown_max_seconds=request.app.state.settings.rate_cooldown_max_seconds,
        )
        if delay:
            raise DomainError(
                "ERR_RATE_LIMIT",
                "Account request limit reached. Retry shortly.",
                429,
                retry_after=delay,
            )
        # Upload admission and its handler revalidate identity in two sessions,
        # but consume only one authenticated-user admission for this request.
        request.state.authenticated_rate_scope = user.id
    return Principal(user, family)


Identity = Annotated[Principal, Depends(principal)]


def upload_preflight(request: Request, business_id: UUID) -> None:
    """Authenticate before buffering a large original; handlers still recheck access."""
    from ledgerpilot.tenancy.service import authorize

    with request.app.state.sessions.begin() as db:
        db.info["http_method"] = request.method
        db.info["request_path"] = request.url.path
        db.info["rate_factory"] = request.app.state.rate_sessions
        db.info["rate_window_seconds"] = request.app.state.settings.rate_window_seconds
        db.info["rate_cooldown_max_seconds"] = request.app.state.settings.rate_cooldown_max_seconds
        who = principal(request, db, request.cookies.get(ACCESS_COOKIE))
        authorize(db, who, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
        request.state.upload_rate_scope = (who.user.id, business_id)
