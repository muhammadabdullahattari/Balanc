"""Disposable real API for frontend acceptance; never serves the development database."""

import asyncio
import json
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from uuid import UUID

import httpx
import test_phase2_integration as identity_tests
import uvicorn
from test_phase3_integration import create_account, setup

from ledgerpilot.documents.worker import run_once
from ledgerpilot.main import create_app

output = Path(sys.argv[1]).resolve()
stop = output.with_suffix(".stop")
fixture = identity_tests.database_settings.__wrapped__()
settings = next(fixture)
credentials = []
original = identity_tests.new_identity


async def capture(browser):
    value = await original(browser)
    credentials.append(value)
    return value


identity_tests.new_identity = capture


async def seed():
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": identity_tests.ORIGIN},
        ) as owner,
    ):
        owner.headers["X-CSRF-Token"] = (await owner.get("/api/v1/auth/csrf")).json()["csrf_token"]
        async with identity_tests.accountant_context(owner, settings) as (bid, api):
            base, accounts, party = await setup(api, bid)
            accounts["equity"] = await create_account(api, base, "opening_equity", "equity")
            accounts["deposit"] = await create_account(api, base, "deposit", "liability")
            accounts["clearing"] = await create_account(api, base, "clearing", "asset", "clearing")
            opening_bid = await identity_tests.new_business(owner)
            invited = await owner.post(
                f"/api/v1/businesses/{opening_bid}/invitations",
                json={"email": credentials[-1][0], "role": "accountant"},
            )
            assert invited.status_code == 202, invited.text
            accepted = await api.post(
                "/api/v1/invitations/accept",
                json={"token": await identity_tests.mail_token(credentials[-1][0])},
            )
            assert accepted.status_code == 200, accepted.text
            opening_base, opening_accounts, opening_party = await setup(api, opening_bid)
            opening_accounts["equity"] = await create_account(
                api, opening_base, "opening_equity", "equity"
            )
            opening_business = (await api.get(opening_base)).json()
            business = (await api.get(base)).json()
            return {
                "actor": (await api.get("/api/v1/auth/me")).json()["id"],
                "business": business,
                "accounts": accounts,
                "party": party,
                "opening_business": opening_business,
                "opening_accounts": opening_accounts,
                "opening_party": opening_party,
                "email": credentials[-1][0],
                "password": credentials[-1][1],
                "recovery": credentials[-1][2],
            }


try:
    data = asyncio.run(seed())
    settings = settings.model_copy(
        update={"frontend_origins": "http://127.0.0.1:8000,http://127.0.0.1:5173"}
    )
    stop.unlink(missing_ok=True)
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=8000, log_level="warning")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data), encoding="utf-8")

    def worker():
        while not stop.exists():
            run_once(settings, UUID(data["actor"]), UUID(data["business"]["id"]))
            time.sleep(0.5)

    threading.Thread(target=worker, daemon=True).start()

    def monitor():
        while not stop.exists():
            time.sleep(0.2)
        server.should_exit = True

    threading.Thread(target=monitor, daemon=True).start()
    server.run()
finally:
    stop.touch()
    if "data" in globals():
        storage = Path(settings.evidence_storage_root).resolve()
        for bid in (data["business"]["id"], data["opening_business"]["id"]):
            tenant = storage / str(UUID(bid))
            assert tenant.resolve().parent == storage
            if tenant.exists():
                for original in tenant.glob("*.bin"):
                    if original.is_file() and not original.is_symlink():
                        original.unlink(missing_ok=True)
                with suppress(OSError):
                    tenant.rmdir()
    fixture.close()
    output.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)
