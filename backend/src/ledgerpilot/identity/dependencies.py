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

ACCESS_COOKIE = "__Host-lp_access"
REFRESH_COOKIE = "__Host-lp_refresh"
CSRF_COOKIE = "__Host-lp_csrf"
SESSION_COOKIE = APIKeyCookie(name=ACCESS_COOKIE, scheme_name="BrowserSession", auto_error=False)


def db_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions.begin() as db:
        db.info["http_method"] = request.method
        db.info["request_path"] = request.url.path
        db.info["rate_factory"] = request.app.state.rate_sessions
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
    family = repository.session_record(db, uid, sid)
    user = service.valid_family(db, family)
    if family is None:
        raise DomainError("ERR_AUTH", "Authentication required.", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(family.csrf_hash, digest(csrf)):
            raise DomainError("ERR_CSRF", "CSRF check failed.", 403)
    return Principal(user, family)


Identity = Annotated[Principal, Depends(principal)]


def upload_preflight(request: Request, business_id: UUID) -> None:
    """Authenticate before buffering a large original; handlers still recheck access."""
    from ledgerpilot.tenancy.service import authorize

    with request.app.state.sessions.begin() as db:
        db.info["http_method"] = request.method
        db.info["request_path"] = request.url.path
        db.info["rate_factory"] = request.app.state.rate_sessions
        who = principal(request, db, request.cookies.get(ACCESS_COOKIE))
        authorize(db, who, business_id, {"owner", "accountant", "bookkeeper", "reviewer"})
        request.state.upload_rate_scope = (who.user.id, business_id)
