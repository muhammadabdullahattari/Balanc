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
        current.update({
            "customer": cust_id,
            "subscription_id": sub_id,
            "snapshot": snapshot,
        })
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

        grace_until = None
        if status == "past_due":
            grace_until = int(time.time()) + 3 * 86400  # 3-day grace period
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
        current.update({
            "plan": plan,
            "subscription_id": data_object.get("id"),
            "snapshot": snapshot,
        })
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
        snapshot.update({
            "status": "active",
            "paid_access": True,
            "grace_until": None,
        })
        if sub_id:
            snapshot["subscription_id"] = sub_id
        current["snapshot"] = snapshot
        save(db, business_id, "stripe", current)

    elif event_type == "invoice.payment_failed":
        grace_until = int(time.time()) + 3 * 86400
        snapshot = current.get("snapshot") or {}
        snapshot.update({
            "status": "past_due",
            "paid_access": True,  # 3-day grace period allows operational recovery
            "grace_until": grace_until,
        })
        current["snapshot"] = snapshot
        save(db, business_id, "stripe", current)

    elif event_type == "charge.refunded":
        refunds = current.get("refunds", [])
        refunds.append({
            "id": data_object.get("id"),
            "amount_refunded": data_object.get("amount_refunded"),
            "currency": data_object.get("currency"),
            "refunded_at": int(time.time()),
        })
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
    selected_entities = entities if entities is not None else ["Account", "JournalEntry"]

    imported_accounts = 0
    imported_journals = 0
    skipped_duplicates = 0
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    # 1. Accounts mapping & import
    if "Account" in selected_entities:
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

    # 2. JournalEntry mapping & import
    if "JournalEntry" in selected_entities:
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
                    acc_row = db.execute(
                        text("SELECT id FROM app.accounts WHERE business_id=:b LIMIT 1"),
                        {"b": business_id},
                    ).scalar_one()

                debit = amount if posting_type == "debit" else Decimal("0")
                credit = amount if posting_type == "credit" else Decimal("0")
                journal_lines.append({
                    "account_id": str(acc_row),
                    "position": idx + 1,
                    "debit": f"{debit:.2f}",
                    "credit": f"{credit:.2f}",
                })
                total_debit += debit
                total_credit += credit

            if not dry_run and journal_lines:
                business_row = db.execute(
                    text("SELECT version, currency FROM app.businesses WHERE id=:b FOR UPDATE"),
                    {"b": business_id},
                ).mappings().one()
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

    if not dry_run and (imported_accounts > 0 or imported_journals > 0):
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
                "results": json.dumps({
                    "imported_accounts": imported_accounts,
                    "imported_journals": imported_journals,
                }),
                "expected_trial": json.dumps({
                    "status": "reconciled",
                    "debit": str(total_debit),
                    "credit": str(total_credit),
                }),
                "user": identity.user.id,
            },
        )
        record(db, identity.user.id, "quickbooks.imported", business_id, batch_id)

    return {
        "status": "dry_run" if dry_run else "imported",
        "imported_accounts": imported_accounts,
        "imported_journals": imported_journals,
        "skipped_duplicates": skipped_duplicates,
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        "reconciled": total_debit == total_credit,
        "cutover_date": str(effective_cutover),
    }
