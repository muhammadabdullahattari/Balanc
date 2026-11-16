"""Sandbox billing and QuickBooks endpoints."""

import contextlib
import hashlib
import hmac
import json
import time
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import jwt
import requests
from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from ledgerpilot.audit.service import record
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


def process_stripe_event(db: DB, settings: Settings, event: dict[str, Any]) -> None:
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})
    if not isinstance(data_object, dict):
        return

    customer_id = data_object.get("customer") or (
        data_object.get("id") if event_type.startswith("customer.") else None
    )
    business_id_str = data_object.get("client_reference_id") or (
        data_object.get("metadata", {}).get("business_id")
        if isinstance(data_object.get("metadata"), dict)
        else None
    )

    business_id: UUID | None = None
    if business_id_str:
        with contextlib.suppress(ValueError, TypeError):
            business_id = UUID(str(business_id_str))

    # Bypass RLS to read and update provider_connections for webhook events
    db.execute(text("SET LOCAL row_security = off"))

    if not business_id and customer_id:
        row = db.execute(
            text("""
                SELECT business_id FROM app.provider_connections
                WHERE provider = 'stripe' AND details->>'customer' = :cust
            """),
            {"cust": str(customer_id)},
        ).scalar_one_or_none()
        if row:
            business_id = row if isinstance(row, UUID) else UUID(str(row))

    if not business_id:
        return

    current = load(db, business_id, "stripe")
    plan = current.get("plan", "starter")
    event_created = int(event.get("created", time.time()))
    last_event_created = int(current.get("last_event_created", 0))

    # Reject stale / out-of-order webhook events from overwriting current subscription state
    if event_created < last_event_created:
        return
    current["last_event_created"] = event_created

    if event_type == "checkout.session.completed":
        sub_id = data_object.get("subscription")
        cust_id = data_object.get("customer") or customer_id
        snapshot = {
            "status": "active",
            "paid_access": True,
            "grace_until": None,
            "plan": plan,
            "subscription_id": sub_id,
        }
        current.update(
            {
                "customer": cust_id,
                "subscription_id": sub_id,
                "snapshot": snapshot,
            }
        )
        save(db, business_id, "stripe", current)

    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        status = data_object.get("status", "active")
        cancel_at_period_end = bool(data_object.get("cancel_at_period_end", False))
        items = (data_object.get("items") or {}).get("data", [])
        if items and isinstance(items, list):
            price_id = items[0].get("price", {}).get("id", "")
            if price_id == settings.stripe_growth_price_id:
                plan = "growth"
            elif price_id == settings.stripe_enterprise_price_id:
                plan = "enterprise"
            elif price_id == settings.stripe_starter_price_id:
                plan = "starter"

        existing_snapshot = current.get("snapshot") or {}
        existing_grace = existing_snapshot.get("grace_until")
        grace_until = None
        if status == "past_due":
            # Preserve original 3-day grace deadline without extending on repeated processing
            grace_until = (
                existing_grace if existing_grace is not None else event_created + 3 * 86400
            )
        paid_access = status in {"active", "trialing"} or (
            grace_until is not None and time.time() < grace_until
        )
        snapshot = {
            "status": status,
            "paid_access": paid_access,
            "grace_until": grace_until,
            "plan": plan,
            "cancel_at_period_end": cancel_at_period_end,
            "subscription_id": data_object.get("id"),
        }
        current.update(
            {
                "plan": plan,
                "subscription_id": data_object.get("id"),
                "snapshot": snapshot,
            }
        )
        save(db, business_id, "stripe", current)

    elif event_type == "customer.subscription.deleted":
        snapshot = {
            "status": "canceled",
            "paid_access": False,
            "grace_until": None,
            "plan": plan,
            "subscription_id": data_object.get("id"),
        }
        current.update({"snapshot": snapshot})
        save(db, business_id, "stripe", current)

    elif event_type == "invoice.payment_succeeded":
        sub_id = data_object.get("subscription")
        snapshot = current.get("snapshot") or {}
        snapshot.update(
            {
                "status": "active",
                "paid_access": True,
                "grace_until": None,
            }
        )
        if sub_id:
            snapshot["subscription_id"] = sub_id
        current["snapshot"] = snapshot
        save(db, business_id, "stripe", current)

    elif event_type == "invoice.payment_failed":
        snapshot = current.get("snapshot") or {}
        existing_grace = snapshot.get("grace_until")
        # Preserve original grace deadline; do not extend on repeated failure webhooks
        grace_until = existing_grace if existing_grace is not None else event_created + 3 * 86400
        snapshot.update(
            {
                "status": "past_due",
                "paid_access": time.time() < grace_until,
                "grace_until": grace_until,
            }
        )
        current["snapshot"] = snapshot
        save(db, business_id, "stripe", current)

    elif event_type == "charge.refunded":
        refunds = current.get("refunds", [])
        refunds.append(
            {
                "id": data_object.get("id"),
                "amount_refunded": data_object.get("amount_refunded"),
                "currency": data_object.get("currency"),
                "refunded_at": int(time.time()),
            }
        )
        current["refunds"] = refunds
        save(db, business_id, "stripe", current)


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
    process_stripe_event(db, settings, event)
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


@router.get("/businesses/{business_id}/billing/subscription")
def get_subscription(
    business_id: UUID, request: Request, identity: Identity, db: DB
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"})
    current = load(db, business_id, "stripe")
    snapshot = current.get("snapshot", {})
    return {
        "status": snapshot.get("status", "unlinked"),
        "plan": current.get("plan", "starter"),
        "paid_access": snapshot.get("paid_access", False),
        "grace_until": snapshot.get("grace_until"),
        "cancel_at_period_end": snapshot.get("cancel_at_period_end", False),
        "subscription_id": current.get("subscription_id"),
        "customer": current.get("customer"),
    }


@router.post("/businesses/{business_id}/billing/subscription/plan")
def change_subscription_plan(
    business_id: UUID,
    new_plan: Literal["starter", "growth", "enterprise"],
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    current = load(db, business_id, "stripe")
    if not current.get("customer") or not current.get("subscription_id"):
        raise DomainError("ERR_NO_SUBSCRIPTION", "No active subscription to change.", 404)

    prices = {
        "starter": settings.stripe_starter_price_id,
        "growth": settings.stripe_growth_price_id,
        "enterprise": settings.stripe_enterprise_price_id,
    }
    price = prices[new_plan]
    if not price.startswith("price_"):
        raise DomainError("ERR_PROVIDER_CONFIG", "Configure the requested plan price.", 503)

    sub = stripe(settings, "GET", f"subscriptions/{current['subscription_id']}")
    item_id = sub["items"]["data"][0]["id"]

    updated = stripe(
        settings,
        "POST",
        f"subscriptions/{current['subscription_id']}",
        data={
            "items[0][id]": item_id,
            "items[0][price]": price,
            "proration_behavior": "create_prorations",
        },
    )
    current["plan"] = new_plan
    snapshot = current.get("snapshot", {})
    snapshot.update({"plan": new_plan, "status": updated["status"]})
    current["snapshot"] = snapshot
    save(db, business_id, "stripe", current)
    return {"plan": new_plan, "status": updated["status"]}


@router.post("/businesses/{business_id}/billing/subscription/cancel")
def cancel_subscription(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    immediate: bool = False,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner"})
    settings: Settings = request.app.state.settings
    current = load(db, business_id, "stripe")
    if not current.get("subscription_id"):
        raise DomainError("ERR_NO_SUBSCRIPTION", "No active subscription to cancel.", 404)

    if immediate:
        stripe(settings, "DELETE", f"subscriptions/{current['subscription_id']}")
        snapshot = current.get("snapshot", {})
        snapshot.update({"status": "canceled", "paid_access": False})
        current["snapshot"] = snapshot
        save(db, business_id, "stripe", current)
        return {"status": "canceled", "paid_access": False}
    else:
        res = stripe(
            settings,
            "POST",
            f"subscriptions/{current['subscription_id']}",
            data={"cancel_at_period_end": "true"},
        )
        snapshot = current.get("snapshot", {})
        snapshot.update({"cancel_at_period_end": True})
        current["snapshot"] = snapshot
        save(db, business_id, "stripe", current)
        return {"status": res.get("status", "active"), "cancel_at_period_end": True}


@router.post("/businesses/{business_id}/quickbooks/import")
def import_quickbooks(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
    cutover_date: date | None = None,
    entities: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    authorize(db, identity, business_id, {"owner", "accountant"}, posting=True)
    settings: Settings = request.app.state.settings
    current = load(db, business_id, "quickbooks")
    if "tokens" not in current or "realm_id" not in current:
        raise DomainError("ERR_PROVIDER_CONFIG", "Connect QuickBooks first.", 409)

    cipher = encryption(settings)
    tokens = json.loads(cipher.decrypt(current["tokens"].encode()))
    realm_id = current["realm_id"]
    access_token = tokens["access_token"]
    effective_cutover = cutover_date or date.today()
    default_entities = [
        "account",
        "customer",
        "vendor",
        "journalentry",
        "invoice",
        "bill",
        "payment",
        "creditmemo",
    ]
    norm_entities = {e.lower() for e in (entities or default_entities)}

    imported_accounts = 0
    imported_parties = 0
    imported_journals = 0
    imported_invoices = 0
    imported_bills = 0
    imported_payments = 0
    imported_credits = 0
    skipped_duplicates = 0
    unmapped_accounts: list[dict[str, str]] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    # 1. Accounts mapping & import
    if norm_entities & {"account", "accounts"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM Account WHERE Active=true ORDERBY Id MAXRESULTS 1000"},
        )
        qb_accounts = res.get("QueryResponse", {}).get("Account", [])
        for qb_acc in qb_accounts:
            code = str(qb_acc.get("AcctNum") or qb_acc.get("Id") or "").strip()
            name = str(qb_acc.get("Name") or f"QB Account {code}").strip()
            classification = qb_acc.get("Classification", "Asset").lower()
            kind_map = {
                "asset": "asset",
                "liability": "liability",
                "equity": "equity",
                "revenue": "income",
                "expense": "expense",
            }
            kind = kind_map.get(classification, "asset")
            control = None
            acc_type = qb_acc.get("AccountType", "").lower()
            if "bank" in acc_type:
                control = "bank"
            elif "receivable" in acc_type:
                control = "receivable"
            elif "payable" in acc_type:
                control = "payable"

            exists = db.execute(
                text("SELECT 1 FROM app.accounts WHERE business_id=:b AND code=:c"),
                {"b": business_id, "c": code},
            ).scalar_one_or_none()
            if not exists and not dry_run:
                db.execute(
                    text("""
                        INSERT INTO app.accounts (
                            id, business_id, code, name, kind, control, active, version
                        )
                        VALUES (gen_random_uuid(), :b, :c, :n, :k, :ctrl, true, 0)
                        ON CONFLICT (business_id, code) DO NOTHING
                    """),
                    {"b": business_id, "c": code, "n": name, "k": kind, "ctrl": control},
                )
                imported_accounts += 1

    # 2. Customers mapping & import
    if norm_entities & {"customer", "customers"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM Customer WHERE Active=true MAXRESULTS 1000"},
        )
        qb_customers = res.get("QueryResponse", {}).get("Customer", [])
        for qb_cust in qb_customers:
            raw_c_name = (
                qb_cust.get("DisplayName")
                or qb_cust.get("CompanyName")
                or f"Customer {qb_cust.get('Id')}"
            )
            c_name = str(raw_c_name).strip()[:160]
            c_email = (qb_cust.get("PrimaryEmailAddr") or {}).get("Address")
            if c_email:
                c_email = str(c_email).strip()[:254]
            exists = db.execute(
                text("SELECT 1 FROM app.parties WHERE business_id=:b AND name=:n"),
                {"b": business_id, "n": c_name},
            ).scalar_one_or_none()
            if not exists and not dry_run:
                db.execute(
                    text("""
                        INSERT INTO app.parties (
                            id, business_id, name, email, roles, active, version
                        )
                        VALUES (gen_random_uuid(), :b, :n, :e, '["customer"]'::jsonb, true, 0)
                    """),
                    {"b": business_id, "n": c_name, "e": c_email},
                )
                imported_parties += 1

    # 3. Vendors mapping & import
    if norm_entities & {"vendor", "vendors", "supplier", "suppliers"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM Vendor WHERE Active=true MAXRESULTS 1000"},
        )
        qb_vendors = res.get("QueryResponse", {}).get("Vendor", [])
        for qb_vend in qb_vendors:
            raw_v_name = (
                qb_vend.get("DisplayName")
                or qb_vend.get("CompanyName")
                or f"Vendor {qb_vend.get('Id')}"
            )
            v_name = str(raw_v_name).strip()[:160]
            v_email = (qb_vend.get("PrimaryEmailAddr") or {}).get("Address")
            if v_email:
                v_email = str(v_email).strip()[:254]
            exists = db.execute(
                text("SELECT 1 FROM app.parties WHERE business_id=:b AND name=:n"),
                {"b": business_id, "n": v_name},
            ).scalar_one_or_none()
            if not exists and not dry_run:
                db.execute(
                    text("""
                        INSERT INTO app.parties (
                            id, business_id, name, email, roles, active, version
                        )
                        VALUES (gen_random_uuid(), :b, :n, :e, '["supplier"]'::jsonb, true, 0)
                    """),
                    {"b": business_id, "n": v_name, "e": v_email},
                )
                imported_parties += 1

    # 4. JournalEntry mapping & import
    if norm_entities & {"journalentry", "journalentries", "journal", "journals"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM JournalEntry ORDERBY Id MAXRESULTS 500"},
        )
        qb_journals = res.get("QueryResponse", {}).get("JournalEntry", [])
        for entry in qb_journals:
            qb_id = entry.get("Id")
            idempotency_key = f"qb:{realm_id}:journal:{qb_id}"

            already_posted = db.execute(
                text("SELECT id FROM app.journals WHERE business_id=:b AND idempotency_key=:k"),
                {"b": business_id, "k": idempotency_key},
            ).scalar_one_or_none()
            if already_posted:
                skipped_duplicates += 1
                continue

            lines = entry.get("Line", [])
            journal_lines = []
            txn_date = entry.get("TxnDate", str(effective_cutover))
            memo = str(entry.get("DocNumber") or f"QuickBooks import {qb_id}")[:200]

            for idx, line in enumerate(lines):
                detail = line.get("JournalEntryLineDetail", {})
                posting_type = detail.get("PostingType", "").lower()
                amount = Decimal(str(line.get("Amount", 0)))
                acc_ref = (detail.get("AccountRef") or {}).get("value")
                acc_name = (detail.get("AccountRef") or {}).get("name", "")

                acc_row: Any = db.execute(
                    text("""
                        SELECT id FROM app.accounts
                        WHERE business_id=:b AND (code=:code OR name=:name)
                    """),
                    {"b": business_id, "code": str(acc_ref), "name": str(acc_name)},
                ).scalar_one_or_none()
                if not acc_row:
                    unmapped_accounts.append(
                        {
                            "code": str(acc_ref or ""),
                            "name": str(acc_name or ""),
                            "journal_id": str(qb_id or ""),
                        }
                    )
                    if not dry_run:
                        err_msg = (
                            f"Unmapped QuickBooks account '{acc_name or acc_ref}' "
                            f"in journal entry '{qb_id}'. Map accounts before importing."
                        )
                        raise DomainError("ERR_UNMAPPED_ACCOUNT", err_msg, 422)

                debit = amount if posting_type == "debit" else Decimal("0")
                credit = amount if posting_type == "credit" else Decimal("0")
                journal_lines.append(
                    {
                        "account_id": str(acc_row) if acc_row else None,
                        "position": idx + 1,
                        "debit": f"{debit:.2f}",
                        "credit": f"{credit:.2f}",
                    }
                )
                total_debit += debit
                total_credit += credit

            if not dry_run and journal_lines:
                business_row = (
                    db.execute(
                        text("SELECT version, currency FROM app.businesses WHERE id=:b FOR UPDATE"),
                        {"b": business_id},
                    )
                    .mappings()
                    .one()
                )
                b_version = business_row["version"]
                currency = business_row["currency"]
                fingerprint = hashlib.sha256(
                    f"{idempotency_key}:{txn_date}:{total_debit}".encode()
                ).hexdigest()

                db.execute(
                    text("""
                        SELECT app.post_journal(
                            :b, :date, :memo, :currency, :key, :hash, :version,
                            CAST(:lines AS jsonb), NULL
                        )
                    """),
                    {
                        "b": business_id,
                        "date": txn_date,
                        "memo": memo,
                        "currency": currency,
                        "key": idempotency_key,
                        "hash": fingerprint,
                        "version": b_version,
                        "lines": json.dumps(journal_lines),
                    },
                )
                imported_journals += 1

    # 5. Invoices mapping & import
    if norm_entities & {"invoice", "invoices"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM Invoice ORDERBY Id MAXRESULTS 500"},
        )
        qb_invoices = res.get("QueryResponse", {}).get("Invoice", [])
        for inv in qb_invoices:
            qb_id = inv.get("Id")
            idempotency_key = f"qb:{realm_id}:invoice:{qb_id}"
            already_posted = db.execute(
                text("SELECT id FROM app.journals WHERE business_id=:b AND idempotency_key=:k"),
                {"b": business_id, "k": idempotency_key},
            ).scalar_one_or_none()
            if already_posted:
                skipped_duplicates += 1
                continue

            cust_name = str((inv.get("CustomerRef") or {}).get("name") or f"Customer {qb_id}")[:160]
            party_id = db.execute(
                text("SELECT id FROM app.parties WHERE business_id=:b AND name=:n"),
                {"b": business_id, "n": cust_name},
            ).scalar_one_or_none()
            if not party_id and not dry_run:
                party_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.parties (
                            id, business_id, name, email, roles, active, version
                        )
                        VALUES (:id, :b, :n, NULL, '["customer"]'::jsonb, true, 0)
                    """),
                    {"id": party_id, "b": business_id, "n": cust_name},
                )
            elif not party_id:
                party_id = uuid4()

            total_amt = Decimal(str(inv.get("TotalAmt", 0)))
            txn_date = inv.get("TxnDate", str(effective_cutover))
            due_date = inv.get("DueDate", txn_date)
            doc_num = str(inv.get("DocNumber") or f"INV-QB-{qb_id}")[:80]

            ar_acc = db.execute(
                text("""
                    SELECT id FROM app.accounts
                    WHERE business_id=:b AND control='receivable' LIMIT 1
                """),
                {"b": business_id},
            ).scalar_one_or_none()
            rev_acc = db.execute(
                text("SELECT id FROM app.accounts WHERE business_id=:b AND kind='income' LIMIT 1"),
                {"b": business_id},
            ).scalar_one_or_none()
            if not ar_acc or not rev_acc:
                unmapped_accounts.append(
                    {
                        "code": "AR/Income",
                        "name": "Receivable/Income",
                        "journal_id": str(qb_id),
                    }
                )
                if not dry_run:
                    err_msg = (
                        "Unmapped receivable or revenue account for QuickBooks "
                        f"invoice '{doc_num}'."
                    )
                    raise DomainError("ERR_UNMAPPED_ACCOUNT", err_msg, 422)

            inv_lines = [
                {
                    "account_id": str(ar_acc),
                    "position": 1,
                    "debit": f"{total_amt:.2f}",
                    "credit": "0.00",
                },
                {
                    "account_id": str(rev_acc),
                    "position": 2,
                    "debit": "0.00",
                    "credit": f"{total_amt:.2f}",
                },
            ]
            total_debit += total_amt
            total_credit += total_amt

            if not dry_run and ar_acc and rev_acc:
                b_row = (
                    db.execute(
                        text("SELECT version, currency FROM app.businesses WHERE id=:b FOR UPDATE"),
                        {"b": business_id},
                    )
                    .mappings()
                    .one()
                )
                fingerprint = hashlib.sha256(
                    f"{idempotency_key}:{txn_date}:{total_amt}".encode()
                ).hexdigest()
                journal_id = db.execute(
                    text("""
                        SELECT app.post_journal(
                            :b, :date, :memo, :currency, :key, :hash, :version,
                            CAST(:lines AS jsonb), NULL
                        )
                    """),
                    {
                        "b": business_id,
                        "date": txn_date,
                        "memo": f"QuickBooks invoice {doc_num}",
                        "currency": b_row["currency"],
                        "key": idempotency_key,
                        "hash": fingerprint,
                        "version": b_row["version"],
                        "lines": json.dumps(inv_lines),
                    },
                ).scalar_one()
                doc_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.trade_documents (
                            id, business_id, kind, number, party_id, accounting_date,
                            due_date, control_account_id, journal_id, total, tax,
                            lines, version
                        )
                        VALUES (
                            :id, :b, 'invoice', :num, :pid, :date,
                            :due, :ctrl, :jid, :tot, 0, '[]'::jsonb, 0
                        )
                    """),
                    {
                        "id": doc_id,
                        "b": business_id,
                        "num": doc_num,
                        "pid": party_id,
                        "date": txn_date,
                        "due": due_date,
                        "ctrl": ar_acc,
                        "jid": journal_id,
                        "tot": total_amt,
                    },
                )
                imported_invoices += 1

    # 6. Bills mapping & import
    if norm_entities & {"bill", "bills"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM Bill ORDERBY Id MAXRESULTS 500"},
        )
        qb_bills = res.get("QueryResponse", {}).get("Bill", [])
        for b_entry in qb_bills:
            qb_id = b_entry.get("Id")
            idempotency_key = f"qb:{realm_id}:bill:{qb_id}"
            already_posted = db.execute(
                text("SELECT id FROM app.journals WHERE business_id=:b AND idempotency_key=:k"),
                {"b": business_id, "k": idempotency_key},
            ).scalar_one_or_none()
            if already_posted:
                skipped_duplicates += 1
                continue

            vend_name = str((b_entry.get("VendorRef") or {}).get("name") or f"Vendor {qb_id}")[:160]
            party_id = db.execute(
                text("SELECT id FROM app.parties WHERE business_id=:b AND name=:n"),
                {"b": business_id, "n": vend_name},
            ).scalar_one_or_none()
            if not party_id and not dry_run:
                party_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.parties (
                            id, business_id, name, email, roles, active, version
                        )
                        VALUES (:id, :b, :n, NULL, '["supplier"]'::jsonb, true, 0)
                    """),
                    {"id": party_id, "b": business_id, "n": vend_name},
                )
            elif not party_id:
                party_id = uuid4()

            total_amt = Decimal(str(b_entry.get("TotalAmt", 0)))
            txn_date = b_entry.get("TxnDate", str(effective_cutover))
            due_date = b_entry.get("DueDate", txn_date)
            doc_num = str(b_entry.get("DocNumber") or f"BILL-QB-{qb_id}")[:80]

            ap_acc = db.execute(
                text("""
                    SELECT id FROM app.accounts
                    WHERE business_id=:b AND control='payable' LIMIT 1
                """),
                {"b": business_id},
            ).scalar_one_or_none()
            exp_acc = db.execute(
                text("SELECT id FROM app.accounts WHERE business_id=:b AND kind='expense' LIMIT 1"),
                {"b": business_id},
            ).scalar_one_or_none()
            if not ap_acc or not exp_acc:
                unmapped_accounts.append(
                    {
                        "code": "AP/Expense",
                        "name": "Payable/Expense",
                        "journal_id": str(qb_id),
                    }
                )
                if not dry_run:
                    raise DomainError(
                        "ERR_UNMAPPED_ACCOUNT",
                        f"Unmapped payable or expense account for QuickBooks bill '{doc_num}'.",
                        422,
                    )

            bill_lines = [
                {
                    "account_id": str(exp_acc),
                    "position": 1,
                    "debit": f"{total_amt:.2f}",
                    "credit": "0.00",
                },
                {
                    "account_id": str(ap_acc),
                    "position": 2,
                    "debit": "0.00",
                    "credit": f"{total_amt:.2f}",
                },
            ]
            total_debit += total_amt
            total_credit += total_amt

            if not dry_run and ap_acc and exp_acc:
                b_row = (
                    db.execute(
                        text("SELECT version, currency FROM app.businesses WHERE id=:b FOR UPDATE"),
                        {"b": business_id},
                    )
                    .mappings()
                    .one()
                )
                fingerprint = hashlib.sha256(
                    f"{idempotency_key}:{txn_date}:{total_amt}".encode()
                ).hexdigest()
                journal_id = db.execute(
                    text("""
                        SELECT app.post_journal(
                            :b, :date, :memo, :currency, :key, :hash, :version,
                            CAST(:lines AS jsonb), NULL
                        )
                    """),
                    {
                        "b": business_id,
                        "date": txn_date,
                        "memo": f"QuickBooks bill {doc_num}",
                        "currency": b_row["currency"],
                        "key": idempotency_key,
                        "hash": fingerprint,
                        "version": b_row["version"],
                        "lines": json.dumps(bill_lines),
                    },
                ).scalar_one()
                doc_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.trade_documents (
                            id, business_id, kind, number, party_id, accounting_date,
                            due_date, control_account_id, journal_id, total, tax,
                            lines, version
                        )
                        VALUES (
                            :id, :b, 'bill', :num, :pid, :date,
                            :due, :ctrl, :jid, :tot, 0, '[]'::jsonb, 0
                        )
                    """),
                    {
                        "id": doc_id,
                        "b": business_id,
                        "num": doc_num,
                        "pid": party_id,
                        "date": txn_date,
                        "due": due_date,
                        "ctrl": ap_acc,
                        "jid": journal_id,
                        "tot": total_amt,
                    },
                )
                imported_bills += 1

    # 7. Payments mapping & import
    if norm_entities & {"payment", "payments"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM Payment ORDERBY Id MAXRESULTS 500"},
        )
        qb_payments = res.get("QueryResponse", {}).get("Payment", [])
        for p_entry in qb_payments:
            qb_id = p_entry.get("Id")
            idempotency_key = f"qb:{realm_id}:payment:{qb_id}"
            already_posted = db.execute(
                text("SELECT id FROM app.journals WHERE business_id=:b AND idempotency_key=:k"),
                {"b": business_id, "k": idempotency_key},
            ).scalar_one_or_none()
            if already_posted:
                skipped_duplicates += 1
                continue

            cust_name = str((p_entry.get("CustomerRef") or {}).get("name") or f"Customer {qb_id}")[
                :160
            ]
            party_id = db.execute(
                text("SELECT id FROM app.parties WHERE business_id=:b AND name=:n"),
                {"b": business_id, "n": cust_name},
            ).scalar_one_or_none()
            if not party_id and not dry_run:
                party_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.parties (
                            id, business_id, name, email, roles, active, version
                        )
                        VALUES (:id, :b, :n, NULL, '["customer"]'::jsonb, true, 0)
                    """),
                    {"id": party_id, "b": business_id, "n": cust_name},
                )
            elif not party_id:
                party_id = uuid4()

            total_amt = Decimal(str(p_entry.get("TotalAmt", 0)))
            txn_date = p_entry.get("TxnDate", str(effective_cutover))
            bank_acc = db.execute(
                text("SELECT id FROM app.accounts WHERE business_id=:b AND control='bank' LIMIT 1"),
                {"b": business_id},
            ).scalar_one_or_none()
            ar_acc = db.execute(
                text("""
                    SELECT id FROM app.accounts
                    WHERE business_id=:b AND control='receivable' LIMIT 1
                """),
                {"b": business_id},
            ).scalar_one_or_none()
            if not bank_acc or not ar_acc:
                unmapped_accounts.append(
                    {
                        "code": "Bank/AR",
                        "name": "Bank/Receivable",
                        "journal_id": str(qb_id),
                    }
                )
                if not dry_run:
                    raise DomainError(
                        "ERR_UNMAPPED_ACCOUNT",
                        f"Unmapped bank or receivable account for QuickBooks payment '{qb_id}'.",
                        422,
                    )

            pmt_lines = [
                {
                    "account_id": str(bank_acc),
                    "position": 1,
                    "debit": f"{total_amt:.2f}",
                    "credit": "0.00",
                },
                {
                    "account_id": str(ar_acc),
                    "position": 2,
                    "debit": "0.00",
                    "credit": f"{total_amt:.2f}",
                },
            ]
            total_debit += total_amt
            total_credit += total_amt

            if not dry_run and bank_acc and ar_acc:
                b_row = (
                    db.execute(
                        text("SELECT version, currency FROM app.businesses WHERE id=:b FOR UPDATE"),
                        {"b": business_id},
                    )
                    .mappings()
                    .one()
                )
                fingerprint = hashlib.sha256(
                    f"{idempotency_key}:{txn_date}:{total_amt}".encode()
                ).hexdigest()
                journal_id = db.execute(
                    text("""
                        SELECT app.post_journal(
                            :b, :date, :memo, :currency, :key, :hash, :version,
                            CAST(:lines AS jsonb), NULL
                        )
                    """),
                    {
                        "b": business_id,
                        "date": txn_date,
                        "memo": f"QuickBooks payment {qb_id}",
                        "currency": b_row["currency"],
                        "key": idempotency_key,
                        "hash": fingerprint,
                        "version": b_row["version"],
                        "lines": json.dumps(pmt_lines),
                    },
                ).scalar_one()
                pmt_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.payments (
                            id, business_id, kind, party_id, accounting_date,
                            bank_account_id, control_account_id, total, fee_amount,
                            journal_id, allocations
                        )
                        VALUES (
                            :id, :b, 'receipt', :pid, :date,
                            :bank, :ctrl, :tot, 0,
                            :jid, '[]'::jsonb
                        )
                    """),
                    {
                        "id": pmt_id,
                        "b": business_id,
                        "pid": party_id,
                        "date": txn_date,
                        "bank": bank_acc,
                        "ctrl": ar_acc,
                        "tot": total_amt,
                        "jid": journal_id,
                    },
                )
                imported_payments += 1

    # 8. CreditMemo mapping & import
    if norm_entities & {"creditmemo", "creditmemos", "credit_memo", "credit_memos"}:
        res = provider(
            "GET",
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"query": "SELECT * FROM CreditMemo ORDERBY Id MAXRESULTS 500"},
        )
        qb_credits = res.get("QueryResponse", {}).get("CreditMemo", [])
        for c_entry in qb_credits:
            qb_id = c_entry.get("Id")
            idempotency_key = f"qb:{realm_id}:credit:{qb_id}"
            already_posted = db.execute(
                text("SELECT id FROM app.journals WHERE business_id=:b AND idempotency_key=:k"),
                {"b": business_id, "k": idempotency_key},
            ).scalar_one_or_none()
            if already_posted:
                skipped_duplicates += 1
                continue

            cust_name = str((c_entry.get("CustomerRef") or {}).get("name") or f"Customer {qb_id}")[
                :160
            ]
            party_id = db.execute(
                text("SELECT id FROM app.parties WHERE business_id=:b AND name=:n"),
                {"b": business_id, "n": cust_name},
            ).scalar_one_or_none()
            if not party_id and not dry_run:
                party_id = uuid4()
                db.execute(
                    text("""
                        INSERT INTO app.parties (
                            id, business_id, name, email, roles, active, version
                        )
                        VALUES (:id, :b, :n, NULL, '["customer"]'::jsonb, true, 0)
                    """),
                    {"id": party_id, "b": business_id, "n": cust_name},
                )
            elif not party_id:
                party_id = uuid4()

            total_amt = Decimal(str(c_entry.get("TotalAmt", 0)))
            txn_date = c_entry.get("TxnDate", str(effective_cutover))
            doc_num = str(c_entry.get("DocNumber") or f"CR-QB-{qb_id}")[:80]

            ar_acc = db.execute(
                text("""
                    SELECT id FROM app.accounts
                    WHERE business_id=:b AND control='receivable' LIMIT 1
                """),
                {"b": business_id},
            ).scalar_one_or_none()
            rev_acc = db.execute(
                text("SELECT id FROM app.accounts WHERE business_id=:b AND kind='income' LIMIT 1"),
                {"b": business_id},
            ).scalar_one_or_none()
            if not ar_acc or not rev_acc:
                unmapped_accounts.append(
                    {
                        "code": "AR/Income",
                        "name": "Receivable/Income",
                        "journal_id": str(qb_id),
                    }
                )
                if not dry_run:
                    err_msg = (
                        "Unmapped receivable or income account for credit memo "
                        f"'{doc_num}'."
                    )
                    raise DomainError("ERR_UNMAPPED_ACCOUNT", err_msg, 422)

            cr_lines = [
                {
                    "account_id": str(rev_acc),
                    "position": 1,
                    "debit": f"{total_amt:.2f}",
                    "credit": "0.00",
                },
                {
                    "account_id": str(ar_acc),
                    "position": 2,
                    "debit": "0.00",
                    "credit": f"{total_amt:.2f}",
                },
            ]
            total_debit += total_amt
            total_credit += total_amt

            if not dry_run and ar_acc and rev_acc:
                b_row = (
                    db.execute(
                        text("SELECT version, currency FROM app.businesses WHERE id=:b FOR UPDATE"),
                        {"b": business_id},
                    )
                    .mappings()
                    .one()
                )
                fingerprint = hashlib.sha256(
                    f"{idempotency_key}:{txn_date}:{total_amt}".encode()
                ).hexdigest()
                journal_id = db.execute(
                    text("""
                        SELECT app.post_journal(
                            :b, :date, :memo, :currency, :key, :hash, :version,
                            CAST(:lines AS jsonb), NULL
                        )
                    """),
                    {
                        "b": business_id,
                        "date": txn_date,
                        "memo": f"QuickBooks credit memo {doc_num}",
                        "currency": b_row["currency"],
                        "key": idempotency_key,
                        "hash": fingerprint,
                        "version": b_row["version"],
                        "lines": json.dumps(cr_lines),
                    },
                ).scalar_one()
                doc_id = uuid4()
                # Find an existing original invoice or create self-reference
                orig_id = (
                    db.execute(
                        text("""
                            SELECT id FROM app.trade_documents
                            WHERE business_id=:b AND party_id=:p LIMIT 1
                        """),
                        {"b": business_id, "p": party_id},
                    ).scalar_one_or_none()
                    or doc_id
                )
                db.execute(
                    text("""
                        INSERT INTO app.trade_documents (
                            id, business_id, kind, number, party_id, accounting_date,
                            due_date, control_account_id, journal_id, total, tax,
                            lines, original_id, version
                        )
                        VALUES (
                            :id, :b, 'customer_credit', :num, :pid, :date,
                            :date, :ctrl, :jid, :tot, 0, '[]'::jsonb, :orig, 0
                        )
                    """),
                    {
                        "id": doc_id,
                        "b": business_id,
                        "num": doc_num,
                        "pid": party_id,
                        "date": txn_date,
                        "ctrl": ar_acc,
                        "jid": journal_id,
                        "tot": total_amt,
                        "orig": orig_id,
                    },
                )
                imported_credits += 1

    total_imported_entities = (
        imported_accounts
        + imported_parties
        + imported_journals
        + imported_invoices
        + imported_bills
        + imported_payments
        + imported_credits
    )
    if not dry_run and total_imported_entities > 0:
        batch_id = uuid4()
        db.execute(
            text("""
                INSERT INTO app.import_batches (
                    id, business_id, file_hash, mode, cutover_date,
                    source, results, expected_trial, created_by
                )
                VALUES (
                    :id, :b, :hash, 'journal', :date,
                    :source, CAST(:results AS jsonb), CAST(:expected AS jsonb), :user
                )
            """),
            {
                "id": batch_id,
                "b": business_id,
                "hash": hashlib.sha256(
                    f"quickbooks:{realm_id}:{int(time.time())}".encode()
                ).hexdigest(),
                "date": effective_cutover,
                "source": f"quickbooks:{realm_id}",
                "results": json.dumps(
                    {
                        "imported_accounts": imported_accounts,
                        "imported_parties": imported_parties,
                        "imported_journals": imported_journals,
                        "imported_invoices": imported_invoices,
                        "imported_bills": imported_bills,
                        "imported_payments": imported_payments,
                        "imported_credits": imported_credits,
                        "skipped_duplicates": skipped_duplicates,
                    }
                ),
                "expected_trial": json.dumps(
                    {
                        "status": "reconciled"
                        if len(unmapped_accounts) == 0
                        else "unmapped_accounts",
                        "debit": str(total_debit),
                        "credit": str(total_credit),
                    }
                ),
                "user": identity.user.id,
            },
        )
        record(db, identity.user.id, "quickbooks.imported", business_id, batch_id)

    return {
        "status": "dry_run" if dry_run else "imported",
        "imported_accounts": imported_accounts,
        "imported_parties": imported_parties,
        "imported_journals": imported_journals,
        "imported_invoices": imported_invoices,
        "imported_bills": imported_bills,
        "imported_payments": imported_payments,
        "imported_credits": imported_credits,
        "skipped_duplicates": skipped_duplicates,
        "unmapped_accounts": unmapped_accounts,
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        "reconciled": total_debit == total_credit and len(unmapped_accounts) == 0,
        "cutover_date": str(effective_cutover),
    }
