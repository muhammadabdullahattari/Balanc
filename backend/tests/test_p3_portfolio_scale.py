"""Benchmark authorized portfolio endpoints against an isolated, migrated PostgreSQL database."""

import hashlib
import json
import os
import runpy
from decimal import Decimal
from statistics import quantiles
from time import perf_counter
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.engine import make_url
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401

from ledgerpilot.local_setup import ROOT, FileSettings, connect, credential_url, read_values


@pytest.mark.skipif(os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Local PostgreSQL")
async def test_large_portfolio(client: httpx.AsyncClient, database_settings: FileSettings) -> None:  # noqa: F811
    async with accountant_context(client, database_settings) as (_, api):
        actor = (await api.get("/api/v1/auth/me")).json()["id"]
        dbname = make_url(database_settings.database_url.get_secret_value()).database
        assert dbname and dbname.startswith("ledgerpilot_test_")
        admin = make_url(
            credential_url(read_values(ROOT / ".env"), "POSTGRES_USER", "POSTGRES_PASSWORD")
        ).set(database=dbname)
        generator = runpy.run_path(str(ROOT / "scripts/generate-p3-portfolio.py"))["generate"]
        businesses = list(generator(499, 20261119))
        tasks = []
        with connect(admin) as conn, conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO app.businesses
                (id,name,currency,currency_precision,timezone,basis,fiscal_month,jurisdiction,policy_version,version)
                VALUES (%s,%s,%s,2,'UTC','accrual',1,'US-test',1,0)""",
                [(b["id"], b["name"], b["currency"]) for b in businesses],
            )
            cur.executemany(
                """INSERT INTO app.memberships
                (business_id,user_id,role,can_post,can_invite,active,version)
                VALUES (%s,%s,'accountant',false,false,true,0)""",
                [(b["id"], actor) for b in businesses],
            )
            cur.executemany(
                """INSERT INTO app.periods
                (id,business_id,start_date,end_date,locked,version)
                VALUES (%s,%s,'2024-01-01','2025-12-31',false,0)""",
                [(b["id"], b["id"]) for b in businesses],
            )
            for b in businesses:
                for issue in b["exceptions"]:
                    tasks.append((uuid4(), b["id"], b["id"], issue["reference"], UUID(actor)))
            cur.execute("SELECT set_config('app.user_id',%s,true)", (actor,))
            with conn.pipeline():
                for task in tasks:
                    cur.execute("SELECT set_config('app.business_id',%s,true)", (task[1],))
                    cur.execute(
                        """INSERT INTO app.close_tasks
                        (id,business_id,period_id,name,assigned_to,required,state,comment,version)
                        VALUES (%s,%s,%s,%s,%s,true,'open','',0)""",
                        task,
                    )
                    cur.execute(
                        """UPDATE app.close_tasks SET prepared_by=assigned_to,
                        state='prepared',version=1 WHERE id=%s""",
                        (task[0],),
                    )

        # Replay each generated transaction through the real guarded posting function.
        with connect(admin) as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT set_config('app.user_id',%s,true)", (actor,))
            for b in businesses:
                accounts = {name: str(uuid4()) for name in ("bank", "receivables", "revenue")}
                cur.execute("SELECT set_config('app.business_id',%s,true)", (b["id"],))
                cur.executemany(
                    """INSERT INTO app.accounts
                    (id,business_id,code,name,kind,active,version)
                    VALUES (%s,%s,%s,%s,%s,true,0)""",
                    [
                        (aid, b["id"], name, name, "income" if name == "revenue" else "asset")
                        for name, aid in accounts.items()
                    ],
                )
                cur.execute(
                    """INSERT INTO app.policy_history
                    (id,business_id,version,effective_date,basis,jurisdiction)
                    VALUES (%s,%s,1,'2024-01-01','accrual','US-test')""",
                    (uuid4(), b["id"]),
                )
                # Generated delayed receipts can fall beyond the source invoice year.
                cur.execute(
                    """INSERT INTO app.periods
                    (id,business_id,start_date,end_date,locked,version)
                    VALUES (%s,%s,'2026-01-01','2026-12-31',false,0)""",
                    (uuid4(), b["id"]),
                )
                journal_args = []
                for version, (day, reference, debit, credit, amount) in enumerate(b["journals"]):
                    lines = json.dumps(
                        [
                            {"account_id": accounts[debit], "debit": amount, "credit": "0"},
                            {"account_id": accounts[credit], "debit": "0", "credit": amount},
                        ]
                    )
                    journal_args.append(
                        (
                            b["id"],
                            day,
                            reference,
                            b["currency"],
                            f"synthetic.{version:06d}",
                            hashlib.sha256(lines.encode()).hexdigest(),
                            version,
                            lines,
                        ),
                    )
                cur.executemany(
                    "SELECT app.post_journal(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", journal_args
                )
            cur.execute(
                """SELECT count(*),coalesce(sum(debit-credit),0) FROM app.journal_lines
                WHERE business_id=ANY(%s::uuid[])""",
                ([b["id"] for b in businesses],),
            )
            count, net = cur.fetchone()
            assert count > 18000 and Decimal(net) == 0
            print(f"portfolio_fixture: {len(businesses) + 1} businesses; lines={count}")

        # Fixture replay can outlast the short-lived access cookie.
        refreshed = await api.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text

        async def timed(label, method, url, **kwargs):
            timings = []
            for _ in range(3 if method == "GET" else 1):
                start = perf_counter()
                response = await api.request(method, url, **kwargs)
                timings.append((perf_counter() - start) * 1000)
                assert response.is_success, response.text
            p95 = (
                quantiles(timings, n=100, method="inclusive")[94]
                if len(timings) > 1
                else timings[0]
            )
            print(f"portfolio_500 {label}: p95={p95:.1f} ms; samples={len(timings)}")
            assert response.is_success, response.text
            return response.json()

        page = await timed("sort", "GET", "/api/v1/portfolio", params={"limit": 25})
        assert len(page) == 25
        assert page[0]["exceptions"] == max(len(b["exceptions"]) for b in businesses)
        later = await timed("page", "GET", "/api/v1/portfolio", params={"offset": 25})
        assert not {r["business_id"] for r in later} & {r["business_id"] for r in page}
        selected = await timed("filter", "GET", "/api/v1/portfolio", params={"status": "clear"})
        assert selected and all(not r["exceptions"] for r in selected)
        task, bid, *_ = tasks[0]
        await timed(
            "assign",
            "POST",
            f"/api/v1/businesses/{bid}/portfolio-exceptions/close/{task}/assignment",
            json={"assignee": actor, "expected_assignee": None},
        )
        assigned = await timed(
            "assignee_filter", "GET", "/api/v1/portfolio", params={"assignee": actor}
        )
        assert [r["business_id"] for r in assigned] == [bid]
