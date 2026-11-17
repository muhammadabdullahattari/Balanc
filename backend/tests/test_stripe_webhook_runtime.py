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
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and Mailpit"
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
