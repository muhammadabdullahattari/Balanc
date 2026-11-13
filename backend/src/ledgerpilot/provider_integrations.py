"""Sandbox billing and QuickBooks endpoints."""

import hashlib
import hmac
import json
import time
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import jwt
import requests
from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from ledgerpilot.config import Settings
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.identity.security import encryption
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize

router = APIRouter(tags=["provider integrations"])


def provider(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = requests.request(method, url, timeout=(4, 15), allow_redirects=False, **kwargs)
        if not response.ok or response.is_redirect:
            raise ValueError
        data = response.json()
        if isinstance(data, dict):
            return data
    except (requests.RequestException, ValueError):
        pass
    raise DomainError("ERR_PROVIDER", "The provider request could not be completed.", 502)


def stripe(settings: Settings, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    key = settings.stripe_secret_key
    if not key or not key.get_secret_value().startswith(("sk_test_", "rk_test_")):
        raise DomainError("ERR_PROVIDER_CONFIG", "Stripe sandbox credentials are required.", 503)
    return provider(
        method, "https://api.stripe.com/v1/" + path, auth=(key.get_secret_value(), ""), **kwargs
    )


def load(db: DB, business_id: UUID, name: str) -> dict[str, Any]:
    return (
        db.execute(
            text("""
        SELECT details FROM app.provider_connections
        WHERE business_id=:business AND provider=:name FOR UPDATE
    """),
            {"business": business_id, "name": name},
        ).scalar_one_or_none()
        or {}
    )


def save(db: DB, business_id: UUID, name: str, data: dict[str, Any]) -> None:
    db.execute(
        text("""
        INSERT INTO app.provider_connections (business_id, provider, details)
        VALUES (:business, :name, CAST(:details AS jsonb))
        ON CONFLICT (business_id, provider) DO UPDATE SET details=excluded.details
    """),
        {"business": business_id, "name": name, "details": json.dumps(data)},
    )


@router.post("/businesses/{business_id}/billing/checkout")
def checkout(
    business_id: UUID,
    plan: Literal["starter", "growth", "enterprise"],
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, str]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    prices = {
        "starter": settings.stripe_starter_price_id,
        "growth": settings.stripe_growth_price_id,
        "enterprise": settings.stripe_enterprise_price_id,
    }
    price = prices[plan]
    destination = settings.billing_return_url
    if not price.startswith("price_") or urlsplit(destination).scheme != "https":
        raise DomainError(
            "ERR_PROVIDER_CONFIG", "Configure a test price and HTTPS billing return URL.", 503
        )
    # Serialize Checkout creation even before the tenant has its first connection row.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"stripe-checkout:{business_id}"},
    )
    connection = load(db, business_id, "stripe")
    if customer := connection.get("customer"):
        subscriptions = stripe(
            settings,
            "GET",
            "subscriptions",
            params={"customer": customer, "status": "all", "limit": 100},
        )
        if subscriptions.get("has_more") or any(
            item["status"] not in {"canceled", "incomplete_expired"}
            for item in subscriptions["data"]
        ):
            raise DomainError("ERR_SUBSCRIPTION_EXISTS", "A subscription already exists.", 409)
    else:
        customer = stripe(
            settings,
            "POST",
            "customers",
            headers={"Idempotency-Key": f"ledgerpilot-customer-{business_id}"},
            data={"metadata[business_id]": str(business_id)},
        )["id"]
    if pending := connection.get("checkout"):
        previous = stripe(settings, "GET", f"checkout/sessions/{pending}")
        if previous["status"] == "open":
            if connection.get("plan") == plan:
                return {"url": str(previous["url"])}
            raise DomainError(
                "ERR_CHECKOUT_PENDING", "Finish the open Checkout session first.", 409
            )
        if previous["status"] == "complete" and not subscriptions["data"]:
            raise DomainError("ERR_CHECKOUT_PENDING", "Checkout is being reconciled.", 409)
    session = stripe(
        settings,
        "POST",
        "checkout/sessions",
        data={
            "mode": "subscription",
            "customer": customer,
            "client_reference_id": str(business_id),
            "line_items[0][price]": price,
            "line_items[0][quantity]": "1",
            "success_url": destination,
            "cancel_url": destination,
        },
    )
    save(db, business_id, "stripe", {"customer": customer, "checkout": session["id"], "plan": plan})
    return {"url": str(session["url"])}


def signed_event(body: bytes, signature: str, secret: str) -> dict[str, Any]:
    try:
        parts = [field.split("=", 1) for field in signature.split(",")]
        timestamp = next(value for key, value in parts if key == "t")
        if abs(time.time() - int(timestamp)) > 300:
            raise ValueError
        expected = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        if not any(key == "v1" and hmac.compare_digest(expected, value) for key, value in parts):
            raise ValueError
        event = json.loads(body)
        if not isinstance(event, dict) or event.get("livemode") is not False:
            raise ValueError
        if not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
            raise ValueError
        return event
    except (ValueError, StopIteration, TypeError):
        raise DomainError("ERR_STRIPE_SIGNATURE", "Invalid Stripe sandbox event.", 400) from None


@router.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request, db: DB) -> dict[str, bool]:
    settings: Settings = request.app.state.settings
    if not settings.stripe_webhook_secret:
        raise DomainError("ERR_PROVIDER_CONFIG", "Stripe signing secret is required.", 503)
    event = signed_event(
        await request.body(),
        request.headers.get("stripe-signature", ""),
        settings.stripe_webhook_secret.get_secret_value(),
    )
    db.execute(
        text("""
        INSERT INTO app.stripe_event_inbox (id, event_type)
        VALUES (:id, :type) ON CONFLICT (id) DO NOTHING
    """),
        {"id": event["id"], "type": event["type"]},
    )
    return {"received": True}


def quickbooks_config(settings: Settings) -> tuple[str, str, str]:
    if (
        not settings.quickbooks_client_id
        or not settings.quickbooks_client_secret
        or urlsplit(settings.quickbooks_redirect_uri).scheme != "https"
    ):
        raise DomainError(
            "ERR_PROVIDER_CONFIG", "Configure Intuit sandbox credentials and HTTPS callback.", 503
        )
    return (
        settings.quickbooks_client_id,
        settings.quickbooks_client_secret.get_secret_value(),
        settings.quickbooks_redirect_uri,
    )


def state_key(settings: Settings) -> str:
    return hmac.new(
        settings.session_signing_key.get_secret_value().encode(),
        b"quickbooks-oauth-state",
        hashlib.sha256,
    ).hexdigest()


@router.post("/businesses/{business_id}/quickbooks/authorize")
def start_quickbooks(
    business_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, str]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    client, _, callback = quickbooks_config(settings)
    nonce = uuid4().hex
    current = load(db, business_id, "quickbooks")
    save(db, business_id, "quickbooks", {**current, "nonce": nonce})
    state = jwt.encode(
        {
            "business": str(business_id),
            "user": str(identity.user.id),
            "nonce": nonce,
            "exp": int(time.time()) + 600,
        },
        state_key(settings),
        algorithm="HS256",
    )
    return {
        "url": "https://appcenter.intuit.com/connect/oauth2?"
        + urlencode(
            {
                "client_id": client,
                "redirect_uri": callback,
                "scope": "com.intuit.quickbooks.accounting",
                "response_type": "code",
                "state": state,
            }
        )
    }


@router.get("/integrations/quickbooks/callback")
def quickbooks_callback(
    request: Request,
    identity: Identity,
    db: DB,
    code: str = Query(max_length=2048),
    state: str = Query(max_length=4096),
    realmId: str = Query(pattern=r"^\d{1,32}$"),
) -> dict[str, bool]:
    settings: Settings = request.app.state.settings
    try:
        claims = jwt.decode(
            state,
            state_key(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "business", "user", "nonce"]},
        )
        business_id = UUID(claims["business"])
        if claims["user"] != str(identity.user.id):
            raise ValueError
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise DomainError(
            "ERR_OAUTH_STATE", "Invalid QuickBooks authorization state.", 400
        ) from None
    authorize(db, identity, business_id, {"owner"})
    current = load(db, business_id, "quickbooks")
    if current.get("nonce") != claims["nonce"]:
        raise DomainError(
            "ERR_OAUTH_STATE", "QuickBooks authorization has expired or was used.", 400
        )
    client, secret, callback = quickbooks_config(settings)
    tokens = provider(
        "POST",
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        auth=(client, secret),
        headers={"Accept": "application/json"},
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": callback},
    )
    encrypted = encryption(settings).encrypt(json.dumps(tokens).encode()).decode()
    save(db, business_id, "quickbooks", {"realm_id": realmId, "tokens": encrypted})
    return {"connected": True}


@router.post("/businesses/{business_id}/quickbooks/refresh")
def refresh_quickbooks(
    business_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, bool]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    client, secret, _ = quickbooks_config(settings)
    current = load(db, business_id, "quickbooks")
    if "tokens" not in current:
        raise DomainError("ERR_PROVIDER_CONFIG", "Connect QuickBooks first.", 409)
    cipher = encryption(settings)
    tokens = json.loads(cipher.decrypt(current["tokens"].encode()))
    refreshed = provider(
        "POST",
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        auth=(client, secret),
        headers={"Accept": "application/json"},
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
    )
    save(
        db,
        business_id,
        "quickbooks",
        {
            **current,
            "tokens": cipher.encrypt(json.dumps(refreshed).encode()).decode(),
        },
    )
    return {"connected": True}


@router.post("/businesses/{business_id}/quickbooks/preview")
def preview_quickbooks(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    entity: Literal[
        "Account",
        "Customer",
        "Vendor",
        "Invoice",
        "Bill",
        "Payment",
        "BillPayment",
        "CreditMemo",
        "VendorCredit",
    ],
    start: int = Query(default=1, ge=1, le=1000000),
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    quickbooks_config(settings)
    current = load(db, business_id, "quickbooks")
    if "tokens" not in current:
        raise DomainError("ERR_PROVIDER_CONFIG", "Connect QuickBooks first.", 409)
    tokens = json.loads(encryption(settings).decrypt(current["tokens"].encode()))
    result = provider(
        "GET",
        f"https://sandbox-quickbooks.api.intuit.com/v3/company/{current['realm_id']}/query",
        headers={"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"},
        params={"query": f"SELECT * FROM {entity} ORDERBY Id STARTPOSITION {start} MAXRESULTS 100"},
    )
    rows = result["QueryResponse"].get(entity, [])
    return {
        "entity": entity,
        "records": rows,
        "next_start": start + 100 if len(rows) == 100 else None,
    }


@router.post("/businesses/{business_id}/billing/reconcile")
def reconcile_billing(
    business_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    current = load(db, business_id, "stripe")
    if not current.get("customer"):
        return {"status": "unlinked", "paid_access": False}
    result = stripe(
        settings,
        "GET",
        "subscriptions",
        params={
            "customer": current["customer"],
            "status": "all",
            "limit": 100,
            "expand[]": "data.latest_invoice",
        },
    )
    relevant = [
        row for row in result["data"] if row["status"] not in {"canceled", "incomplete_expired"}
    ]
    if result.get("has_more") or len(relevant) > 1:
        raise DomainError("ERR_BILLING_REVIEW", "Multiple subscriptions require review.", 409)
    subscription = relevant[0] if relevant else None
    status = subscription["status"] if subscription else "expired"
    invoice = (subscription or {}).get("latest_invoice")
    grace_until = None
    if status == "past_due" and isinstance(invoice, dict) and invoice.get("attempted"):
        finalized = (invoice.get("status_transitions") or {}).get("finalized_at")
        if isinstance(finalized, int):
            grace_until = finalized + 3 * 86400
    access = status in {"active", "trialing"} or (
        grace_until is not None and time.time() < grace_until
    )
    snapshot = {"status": status, "paid_access": access, "grace_until": grace_until}
    save(db, business_id, "stripe", {**current, "snapshot": snapshot})
    return snapshot
