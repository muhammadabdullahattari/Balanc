"""Verify signed webhook intake under the restricted runtime role."""

import hashlib
import hmac
import json
import os
import time
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from test_phase2_integration import client, database_settings, new_business, new_identity

from ledgerpilot.database import set_context
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# Fixtures imported for pytest discovery.
# ruff: noqa: F401, F811
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and SMTP"
)


async def test_stripe_webhook_scoped_role_and_duplicate(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    await new_identity(client)
    bid = UUID(await new_business(client))
    actor = UUID((await client.get("/api/v1/auth/me")).json()["id"])
    secret = "isolated-webhook-test-secret"
    app = create_app(
        database_settings.model_copy(update={"stripe_webhook_secret": SecretStr(secret)})
    )
    async with app.router.lifespan_context(app):
        customer = f"cus_{uuid4().hex}"
        with app.state.sessions.begin() as db:
            set_context(db, actor, bid)
            db.execute(
                text("""INSERT INTO app.provider_connections(business_id,provider,details)
                    VALUES(:bid,'stripe',CAST(:data AS jsonb))"""),
                {"bid": bid, "data": json.dumps({"customer": customer})},
            )
        stamp = int(time.time())
        body = json.dumps(
            {
                "id": f"evt_{uuid4().hex}",
                "created": stamp,
                "type": "charge.refunded",
                "livemode": False,
                "data": {"object": {"id": "ch_test", "customer": customer, "amount_refunded": 100}},
            }
        ).encode()
        signature = hmac.new(
            secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://api.ledgerpilot.test"
        ) as api:
            for _ in range(2):
                r = await api.post(
                    "/api/v1/billing/stripe/webhook",
                    content=body,
                    headers={"Stripe-Signature": f"t={stamp},v1={signature}"},
                )
                assert r.status_code == 200, r.text
        with app.state.sessions.begin() as db:
            set_context(db, actor, bid)
            refunds = db.execute(
                text("""
                SELECT details->'refunds' FROM app.provider_connections
                WHERE business_id=:b AND provider='stripe'
            """),
                {"b": bid},
            ).scalar_one()
            assert len(refunds) == 1


async def test_onboarding_checkout_and_business_creation_under_real_postgres_rls(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    from typing import Any
    from unittest.mock import patch

    from test_phase2_integration import ORIGIN

    app = create_app(
        database_settings.model_copy(
            update={
                "stripe_secret_key": SecretStr("sk_test_12345"),
                "stripe_starter_price_id": "price_1UdmbCDtDvP8U0RHDYhhJuCm",
                "stripe_growth_price_id": "price_1UdmbDDtDvP8U0RHPuuhkV6P",
                "stripe_enterprise_price_id": "price_1UdmbEDtDvP8U0RHh5ZnHwfB",
                "billing_return_url": "https://balanc-amber.vercel.app/settings/billing",
            }
        )
    )

    def fake_stripe(settings: Any, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if method == "POST" and path == "checkout/sessions":
            return {
                "id": "cs_test_real_rls_123",
                "url": "https://checkout.stripe.com/c/pay/cs_test_real_rls_123",
            }
        if method == "GET" and path == "checkout/sessions/cs_test_real_rls_123":
            return {
                "id": "cs_test_real_rls_123",
                "status": "complete",
                "payment_status": "paid",
                "customer": "cus_real_123",
                "amount_total": 50000,
                "currency": "usd",
                "subscription": {
                    "id": "sub_real_123",
                    "status": "active",
                    "current_period_end": 1826668800,
                    "latest_invoice": {
                        "id": "in_real_123",
                        "number": "INV-2026-0001",
                        "amount_paid": 50000,
                        "currency": "usd",
                        "hosted_invoice_url": "https://invoice.stripe.com/i/acct/real123",
                        "invoice_pdf": "https://pay.stripe.com/invoice/real123/pdf",
                    },
                },
            }
        return {"id": "ok"}

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.199", 12345)),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": ORIGIN},
        ) as api,
    ):
        api.headers["X-CSRF-Token"] = (await api.get("/api/v1/auth/csrf")).json()["csrf_token"]
        await new_identity(api)
        with patch("ledgerpilot.provider_integrations.stripe", side_effect=fake_stripe):
            res = await api.post("/api/v1/billing/onboarding/checkout", params={"plan": "growth"})
            assert res.status_code == 200, res.text
            assert res.json()["session_id"] == "cs_test_real_rls_123"
            assert res.json()["url"] == "https://checkout.stripe.com/c/pay/cs_test_real_rls_123"

            status = await api.get(
                "/api/v1/billing/onboarding/status",
                params={"session_id": "cs_test_real_rls_123"},
            )
            assert status.status_code == 200, status.text
            body = status.json()
            assert body["has_paid_plan"] is True
            assert body["plan"] == "growth"
            assert body["invoice_pdf"] == "https://pay.stripe.com/invoice/real123/pdf"

            created = await api.post(
                "/api/v1/businesses",
                json={
                    "name": "RLS Verified Yearly Business",
                    "currency": "USD",
                    "timezone": "UTC",
                    "basis": "accrual",
                    "fiscal_month": 1,
                    "jurisdiction": "US-GAAP",
                    "checkout_session_id": "cs_test_real_rls_123",
                },
            )
            assert created.status_code == 201, created.text
            new_bid = created.json()["id"]

            biz = await api.get(f"/api/v1/businesses/{new_bid}")
            assert biz.status_code == 200, biz.text
            bdata = biz.json()
            assert bdata["p2_projects_available"] is True
            assert bdata["p2_insights_available"] is True
            assert bdata["p2_reconciliation_available"] is True
            assert bdata["p2_prepaids_available"] is True
            assert bdata["p2_micro_close_available"] is True
            assert bdata["p2_retention_available"] is True
            assert bdata["p2_audit_available"] is True

            sub = await api.get(f"/api/v1/businesses/{new_bid}/billing/subscription")
            assert sub.status_code == 200, sub.text
            assert sub.json()["status"] == "active"
            assert sub.json()["paid_access"] is True
            assert sub.json()["plan"] == "growth"

            for suffix in (
                "/retention",
                "/retention/cleanup/policies",
                "/retention/cleanup/requests",
                "/retention/disposal/exports",
                "/audit-grants",
                "/projects",
                "/prepaids",
            ):
                feat_res = await api.get(f"/api/v1/businesses/{new_bid}{suffix}")
                assert feat_res.status_code == 200, (suffix, feat_res.text)
