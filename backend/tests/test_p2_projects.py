"""Verify project attribution and rebilling against PostgreSQL and the posted ledger."""

import os
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401
from test_phase3_integration import command, setup

from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app

# ruff: noqa: F811 -- shared real-service fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires local PostgreSQL and Mailpit"
)


async def test_project_cost_reservation_releases_or_posts_exactly_once(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        denied = await api.get(base + "/projects")
        assert denied.status_code == 404, denied.text
        bill = await api.post(
            base + "/trade-documents",
            json={
                **await command(api, base),
                "kind": "bill",
                "number": "P2 project cost",
                "party_id": party,
                "accounting_date": "2026-10-02",
                "due_date": "2026-10-31",
                "control_account_id": accounts["ap"],
                "lines": [
                    {
                        "account_id": accounts["cost"],
                        "description": "Project expense",
                        "amount": "50",
                    }
                ],
            },
        )
        assert bill.status_code == 201, bill.text
        settings = database_settings.model_copy(
            update={"p2_project_business_ids": frozenset({UUID(bid)})}
        )
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://api.ledgerpilot.test",
                cookies=api.cookies,
                headers=dict(api.headers),
            ) as scoped,
        ):
            project = await scoped.post(
                base + "/projects", json={"name": "Synthetic project", "customer_id": party}
            )
            assert project.status_code == 201, project.text
            pid = project.json()["id"]
            dates = {"start": "2026-10-01", "end": "2026-10-31"}
            lines = await scoped.get(base + "/projects/ledger-lines", params=dates)
            assert lines.status_code == 200, lines.text
            source = lines.json()["items"][0]
            assert source["eligible"] and Decimal(source["debit"]) == 50
            tagged = await scoped.post(
                base + f"/projects/ledger-lines/{source['id']}/tag",
                json={"project_id": pid, "location": "Workshop", "billable": True},
            )
            assert tagged.status_code == 200, tagged.text
            blocked = await scoped.post(
                f"/api/v1/businesses/{uuid4()}/projects/ledger-lines/{source['id']}/tag",
                json={"project_id": pid, "billable": True},
            )
            assert blocked.status_code == 404
            totals = await scoped.get(base + f"/projects/{pid}/totals", params=dates)
            assert {key: Decimal(value) for key, value in totals.json().items()} == {
                "income": 0,
                "cost": 50,
                "margin": -50,
            }
            request = {
                "source_line_id": source["id"],
                "idempotency_key": str(uuid4()),
                "number": "P2 billable cost",
                "description": "Expense reimbursement",
                "accounting_date": "2026-10-03",
                "due_date": "2026-10-31",
                "control_account_id": accounts["ar"],
                "income_account_id": accounts["income"],
            }
            draft_url = base + "/projects/billable-drafts"
            draft = await scoped.post(draft_url, json=request)
            assert draft.status_code == 201, draft.text
            replay = await scoped.post(draft_url, json=request)
            assert replay.status_code == 201 and replay.json()["id"] == draft.json()["id"]
            second = {**request, "idempotency_key": str(uuid4())}
            duplicate = await scoped.post(draft_url, json=second)
            assert duplicate.status_code == 409, duplicate.text
            voided = await scoped.post(
                base + f"/source-drafts/{draft.json()['id']}/void",
                json={
                    "expected_version": 0,
                    "expected_business_version": (await scoped.get(base)).json()["version"],
                },
            )
            assert voided.status_code == 200, voided.text
            replacement = await scoped.post(draft_url, json=second)
            assert replacement.status_code == 201, replacement.text
            issue_url = base + f"/source-drafts/{replacement.json()['id']}/issue"
            approval = {
                "expected_version": 0,
                "expected_business_version": (await scoped.get(base)).json()["version"],
            }
            posted = await scoped.post(issue_url, json=approval)
            assert posted.status_code == 200, posted.text
            again = await scoped.post(issue_url, json=approval)
            assert again.json()["id"] == posted.json()["id"]
            duplicate = await scoped.post(
                draft_url, json={**request, "idempotency_key": str(uuid4())}
            )
            assert duplicate.status_code == 409, duplicate.text
            totals = await scoped.get(base + f"/projects/{pid}/totals", params=dates)
            assert totals.status_code == 200, totals.text
            assert {key: Decimal(value) for key, value in totals.json().items()} == {
                "income": 50,
                "cost": 50,
                "margin": 0,
            }
            rows = (await scoped.get(base + "/projects/ledger-lines", params=dates)).json()["items"]
            assert (
                next(row for row in rows if row["id"] == source["id"])["billing_state"] == "billed"
            )
