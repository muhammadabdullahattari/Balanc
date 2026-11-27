"""E7: External Public Developer API (/public/v1) with scoped tokens & HMAC webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.request
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import APIRouter, Header, Query, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import set_context
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.reports import service as report_service
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize

mgmt_router = APIRouter(prefix="/businesses/{business_id}/developer", tags=["developer-platform"])
public_router = APIRouter(prefix="/public/v1", tags=["public-api"])

_TOKEN_BUCKETS: dict[str, list[float]] = {}

VALID_SCOPES = {
    "read:accounts",
    "read:journals",
    "read:reports",
    "write:drafts",
}


def _cipher(settings: Settings) -> Fernet:
    if settings.evidence_encryption_key:
        return Fernet(settings.evidence_encryption_key.get_secret_value().encode("utf-8"))
    import base64

    derived = hashlib.sha256(
        settings.session_signing_key.get_secret_value().encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


class ApiKeyCreateInput(Schema):
    name: str = Field(min_length=2, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=10)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=5000)
    expires_in_days: int | None = Field(default=90, ge=1, le=365)


class WebhookEndpointCreateInput(Schema):
    target_url: str = Field(min_length=8, max_length=500)
    description: str = Field(default="", max_length=200)
    events: list[str] = Field(min_length=1, max_length=10)


class PublicDraftCreateInput(Schema):
    key: str = Field(min_length=2, max_length=80)
    kind: str = Field(default="invoice", pattern=r"^(invoice|bill|expense)$")
    vendor_or_customer: str = Field(min_length=1, max_length=160)
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    memo: str = Field(default="", max_length=240)


def _authenticate_public_token(
    db: Session,
    authorization: str | None,
    required_scope: str,
) -> tuple[UUID, UUID, UUID]:
    """Verify Bearer lp_pat_..., enforce revocation/expiry/scope/rate-limit, and set RLS context."""
    if not authorization or not authorization.startswith("Bearer lp_pat_"):
        raise DomainError("ERR_AUTH", "Provide a valid Bearer lp_pat_... developer token.", 401)
    raw_token = authorization.removeprefix("Bearer ").strip()
    tok_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    row = (
        db.execute(
            text("SELECT * FROM app.lookup_public_api_key(:h)"),
            {"h": tok_hash},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DomainError("ERR_AUTH", "Invalid developer API token.", 401)
    if row["revoked_at"] is not None:
        raise DomainError("ERR_AUTH", "This developer API token has been revoked.", 401)
    if row["expires_at"] is not None and row["expires_at"] <= datetime.now(UTC):
        raise DomainError("ERR_AUTH", "This developer API token has expired.", 401)

    scopes = list(row["scopes"] or [])
    if required_scope not in scopes:
        raise DomainError(
            "ERR_FORBIDDEN",
            f"Token lacks required scope '{required_scope}'.",
            403,
        )

    # Per-token sliding-window rate limiter
    now_mono = time.monotonic()
    limit_rpm = int(row["rate_limit_per_minute"] or 120)
    bucket = [ts for ts in _TOKEN_BUCKETS.get(tok_hash, []) if now_mono - ts < 60.0]
    if len(bucket) >= limit_rpm:
        raise DomainError(
            "ERR_RATE_LIMIT", "Public API token rate limit exceeded.", 429, retry_after=10
        )
    bucket.append(now_mono)
    _TOKEN_BUCKETS[tok_hash] = bucket

    bid = UUID(str(row["business_id"]))
    uid = UUID(str(row["created_by"]))
    kid = UUID(str(row["key_id"]))
    set_context(db, uid, bid)
    db.execute(
        text(
            "UPDATE app.public_api_keys SET last_used_at = now() "
            "WHERE business_id = :b AND id = :id"
        ),
        {"b": bid, "id": kid},
    )
    return bid, uid, kid


@mgmt_router.get("")
def get_developer_platform_state(
    business_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """List workspace public API keys (masked), webhook subscriptions, and recent deliveries."""
    authorize(db, identity, business_id, {"owner", "accountant"})
    keys_rows = (
        db.execute(
            text(
                """
                SELECT id, name, key_prefix, scopes, rate_limit_per_minute,
                       expires_at, revoked_at, last_used_at, created_at
                FROM app.public_api_keys
                WHERE business_id = :bid
                ORDER BY created_at DESC
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    hooks = (
        db.execute(
            text(
                """
                SELECT id, target_url, description, events, active, created_at
                FROM app.webhook_endpoints
                WHERE business_id = :bid
                ORDER BY created_at DESC
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    deliveries = (
        db.execute(
            text(
                """
                SELECT id, endpoint_id, event_type, signature_header,
                       status, http_status, attempts, created_at
                FROM app.webhook_deliveries
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 30
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    return {
        "available_scopes": sorted(VALID_SCOPES),
        "api_keys": [
            {
                "id": str(r["id"]),
                "name": str(r["name"]),
                "key_prefix": str(r["key_prefix"]),
                "scopes": list(r["scopes"] or []),
                "rate_limit_per_minute": int(r["rate_limit_per_minute"]),
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "revoked": r["revoked_at"] is not None,
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            }
            for r in keys_rows
        ],
        "webhooks": [
            {
                "id": str(h["id"]),
                "target_url": str(h["target_url"]),
                "description": str(h["description"]),
                "events": list(h["events"] or []),
                "active": bool(h["active"]),
            }
            for h in hooks
        ],
        "deliveries": [
            {
                "id": str(d["id"]),
                "endpoint_id": str(d["endpoint_id"]),
                "event_type": str(d["event_type"]),
                "signature_header": str(d["signature_header"]),
                "status": str(d["status"]),
                "http_status": d["http_status"],
                "attempts": int(d["attempts"]),
                "created_at": d["created_at"].isoformat(),
            }
            for d in deliveries
        ],
    }


@mgmt_router.post("/api-keys", status_code=201)
def create_public_api_key(
    business_id: UUID,
    body: ApiKeyCreateInput,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Mint a scoped lp_pat_... Personal Access Token; plaintext is returned only once."""
    authorize(db, identity, business_id, {"owner"})
    invalid = [s for s in body.scopes if s not in VALID_SCOPES]
    if invalid:
        raise DomainError("ERR_VALIDATION", f"Unsupported API scope(s): {', '.join(invalid)}", 422)

    raw_token = f"lp_pat_{secrets.token_urlsafe(32)}"
    prefix = raw_token[:14]
    tok_sha = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    exp_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    kid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.public_api_keys (
              id, business_id, name, key_prefix, key_sha256,
              scopes, rate_limit_per_minute, expires_at, created_by
            ) VALUES (
              :id, :bid, :name, :pref, :sha,
              CAST(:scopes AS jsonb), :rpm, :exp, :uid
            )
            """
        ),
        {
            "id": kid,
            "bid": business_id,
            "name": body.name.strip(),
            "pref": prefix,
            "sha": tok_sha,
            "scopes": json.dumps(sorted(set(body.scopes))),
            "rpm": body.rate_limit_per_minute,
            "exp": exp_at,
            "uid": identity.user.id,
        },
    )
    record(db, identity.user.id, "developer.api_key_created", business_id, kid)
    return {
        "id": str(kid),
        "name": body.name.strip(),
        "token": raw_token,
        "key_prefix": prefix,
        "scopes": sorted(set(body.scopes)),
        "rate_limit_per_minute": body.rate_limit_per_minute,
        "expires_at": exp_at.isoformat() if exp_at else None,
    }


@mgmt_router.post("/api-keys/{key_id}/revoke")
def revoke_public_api_key(
    business_id: UUID,
    key_id: UUID,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Immediately revoke a public API token."""
    authorize(db, identity, business_id, {"owner"})
    res = (
        db.execute(
            text(
                """
            UPDATE app.public_api_keys
            SET revoked_at = coalesce(revoked_at, now())
            WHERE business_id = :bid AND id = :id
            RETURNING id, revoked_at
            """
            ),
            {"bid": business_id, "id": key_id},
        )
        .mappings()
        .first()
    )
    if res is None:
        raise DomainError("ERR_NOT_FOUND", "API key not found.", 404)
    record(db, identity.user.id, "developer.api_key_revoked", business_id, key_id)
    return {"id": str(key_id), "revoked": True, "revoked_at": res["revoked_at"].isoformat()}


@mgmt_router.post("/webhooks", status_code=201)
def register_webhook_endpoint(
    business_id: UUID,
    body: WebhookEndpointCreateInput,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Register an outbound HMAC-signed webhook endpoint for workspace events."""
    authorize(db, identity, business_id, {"owner", "accountant"})
    if not body.target_url.startswith("https://"):
        raise DomainError("ERR_VALIDATION", "Webhook target URL must use HTTPS.", 422)
    signing_secret = f"whsec_{secrets.token_urlsafe(24)}"
    enc_secret = (
        _cipher(request.app.state.settings).encrypt(signing_secret.encode("utf-8")).decode("ascii")
    )
    wid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.webhook_endpoints (
              id, business_id, target_url, description, signing_secret_encrypted,
              events, active, created_by
            ) VALUES (
              :id, :bid, :url, :desc, :sec,
              CAST(:ev AS jsonb), true, :uid
            )
            """
        ),
        {
            "id": wid,
            "bid": business_id,
            "url": body.target_url.strip(),
            "desc": body.description.strip(),
            "sec": enc_secret,
            "ev": json.dumps(body.events),
            "uid": identity.user.id,
        },
    )
    return {
        "id": str(wid),
        "target_url": body.target_url.strip(),
        "signing_secret": signing_secret,
        "events": body.events,
        "active": True,
    }


@mgmt_router.post("/webhooks/{endpoint_id}/test", status_code=201)
def dispatch_webhook_event(
    business_id: UUID,
    endpoint_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    event_type: str = Query("draft.created", max_length=60),
) -> dict[str, Any]:
    """Sign an outbound webhook payload with HMAC-SHA256 and deliver/record retry state."""
    authorize(db, identity, business_id, {"owner", "accountant"})
    ep = (
        db.execute(
            text("SELECT * FROM app.webhook_endpoints WHERE business_id = :b AND id = :id"),
            {"b": business_id, "id": endpoint_id},
        )
        .mappings()
        .first()
    )
    if ep is None:
        raise DomainError("ERR_NOT_FOUND", "Webhook endpoint not found.", 404)

    secret_plain = (
        _cipher(request.app.state.settings)
        .decrypt(str(ep["signing_secret_encrypted"]).encode("ascii"))
        .decode("utf-8")
    )
    event_payload = {
        "id": f"evt_{uuid4().hex[:16]}",
        "event": event_type,
        "business_id": str(business_id),
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    raw_bytes = json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = "sha256=" + hmac.new(secret_plain.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    deliv_status = "simulated_local"
    http_code: int | None = 200
    next_retry = None
    if request.app.state.settings.app_env not in {"local", "test"}:
        req = urllib.request.Request(
            str(ep["target_url"]),
            data=raw_bytes,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-LedgerPilot-Signature": sig,
                "User-Agent": "LedgerPilot-Webhooks/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                http_code = resp.status
                deliv_status = "delivered" if 200 <= resp.status < 300 else "queued_retry"
        except Exception:
            http_code = None
            deliv_status = "queued_retry"
            next_retry = datetime.now(UTC) + timedelta(minutes=5)

    did = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.webhook_deliveries (
              id, business_id, endpoint_id, event_type, payload_json,
              signature_header, status, http_status, attempts, next_retry_at
            ) VALUES (
              :id, :bid, :eid, :ev, CAST(:pl AS jsonb),
              :sig, :st, :code, 1, :nret
            )
            """
        ),
        {
            "id": did,
            "bid": business_id,
            "eid": endpoint_id,
            "ev": event_type,
            "pl": json.dumps(event_payload),
            "sig": sig,
            "st": deliv_status,
            "code": http_code,
            "nret": next_retry,
        },
    )
    return {
        "delivery_id": str(did),
        "event_type": event_type,
        "signature_header": sig,
        "status": deliv_status,
        "http_status": http_code,
    }


# ---------------------------------------------------------------------------
# Public External Developer API (/public/v1/*)
# ---------------------------------------------------------------------------


@public_router.get("/openapi.json")
def public_developer_openapi() -> dict[str, Any]:
    """Published OpenAPI 3.1 contract for external LedgerPilot developers."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "LedgerPilot Public Developer API",
            "version": "1.0.0",
            "description": (
                "Scoped Bearer-token API for reading ledger accounts, journals, and financial "
                "reports, and submitting reviewable drafts (human approval required to post)."
            ),
        },
        "components": {
            "securitySchemes": {
                "PersonalAccessToken": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "lp_pat",
                }
            }
        },
        "security": [{"PersonalAccessToken": []}],
        "paths": {
            "/public/v1/accounts": {"get": {"summary": "List workspace chart of accounts"}},
            "/public/v1/journals": {"get": {"summary": "List immutable posted journals"}},
            "/public/v1/reports/profit-loss": {"get": {"summary": "Read Profit & Loss statement"}},
            "/public/v1/reports/balance-sheet": {"get": {"summary": "Read Balance Sheet"}},
            "/public/v1/drafts": {"post": {"summary": "Submit a reviewable document draft"}},
        },
    }


@public_router.get(
    "/accounts",
    openapi_extra={"security": [{"PersonalAccessToken": []}]},
)
def public_list_accounts(
    db: DB,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    bid, _uid, _kid = _authenticate_public_token(db, authorization, "read:accounts")
    rows = (
        db.execute(
            text(
                "SELECT id, code, name, kind, active FROM app.accounts "
                "WHERE business_id = :b ORDER BY code ASC LIMIT 200"
            ),
            {"b": bid},
        )
        .mappings()
        .all()
    )
    return {
        "business_id": str(bid),
        "items": [
            {
                "id": str(r["id"]),
                "code": str(r["code"]),
                "name": str(r["name"]),
                "kind": str(r["kind"]),
                "active": bool(r["active"]),
            }
            for r in rows
        ],
    }


@public_router.get(
    "/journals",
    openapi_extra={"security": [{"PersonalAccessToken": []}]},
)
def public_list_journals(
    db: DB,
    authorization: str | None = Header(default=None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    bid, _uid, _kid = _authenticate_public_token(db, authorization, "read:journals")
    rows = (
        db.execute(
            text(
                "SELECT id, accounting_date, memo, currency, status FROM app.journals "
                "WHERE business_id = :b AND status = 'posted' "
                "ORDER BY accounting_date DESC LIMIT :lim"
            ),
            {"b": bid, "lim": limit},
        )
        .mappings()
        .all()
    )
    return {
        "business_id": str(bid),
        "items": [
            {
                "id": str(r["id"]),
                "accounting_date": r["accounting_date"].isoformat(),
                "memo": str(r["memo"] or ""),
                "currency": str(r["currency"]),
                "status": str(r["status"]),
            }
            for r in rows
        ],
    }


@public_router.get(
    "/reports/profit-loss",
    openapi_extra={"security": [{"PersonalAccessToken": []}]},
)
def public_profit_loss(
    db: DB,
    start: date,
    end: date,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    bid, _uid, _kid = _authenticate_public_token(db, authorization, "read:reports")
    return report_service.financial(db, bid, start, end, "accrual", "profit-loss")


@public_router.get(
    "/reports/balance-sheet",
    openapi_extra={"security": [{"PersonalAccessToken": []}]},
)
def public_balance_sheet(
    db: DB,
    as_of: date,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    bid, _uid, _kid = _authenticate_public_token(db, authorization, "read:reports")
    return report_service.financial(db, bid, date(1900, 1, 1), as_of, "accrual", "balance-sheet")


@public_router.post(
    "/drafts",
    status_code=201,
    openapi_extra={"security": [{"PersonalAccessToken": []}]},
)
def public_create_reviewable_draft(
    body: PublicDraftCreateInput,
    db: DB,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Create a reviewable draft via API — never posts directly without human approval in UI."""
    bid, uid, _kid = _authenticate_public_token(db, authorization, "write:drafts")
    did = uuid4()
    payload = {
        "kind": body.kind,
        "vendor_or_customer": body.vendor_or_customer.strip(),
        "amount": body.amount.strip(),
        "currency": body.currency.upper(),
        "memo": body.memo.strip(),
        "channel": "public_api",
    }
    db.execute(
        text(
            """
            INSERT INTO app.source_drafts (
              id, business_id, key, payload, status, version, prepared_by
            ) VALUES (
              :id, :bid, :key, CAST(:pl AS jsonb), 'draft', 0, :uid
            )
            ON CONFLICT (business_id, key) DO NOTHING
            """
        ),
        {
            "id": did,
            "bid": bid,
            "key": f"api:{body.key.strip()}",
            "pl": json.dumps(payload),
            "uid": uid,
        },
    )
    resolved_id = db.scalar(
        text("SELECT id FROM app.source_drafts WHERE business_id = :b AND key = :k"),
        {"b": bid, "k": f"api:{body.key.strip()}"},
    )
    return {
        "id": str(resolved_id or did),
        "status": "draft",
        "human_approval_required": True,
        "payload": payload,
    }
