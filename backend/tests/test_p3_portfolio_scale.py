"""Benchmark authorized portfolio endpoints against an isolated, migrated PostgreSQL database."""

import os
import runpy
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

        async def timed(label, method, url, **kwargs):
            start = perf_counter()
            response = await api.request(method, url, **kwargs)
            print(f"portfolio_500 {label}: {(perf_counter() - start) * 1000:.1f} ms")
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
