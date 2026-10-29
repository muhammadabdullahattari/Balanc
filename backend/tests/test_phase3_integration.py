"""Phase 3 golden workflows on real PostgreSQL, real SMTP and the real API."""

import os

# ruff: noqa: F811 -- pytest injects the imported real-service fixtures
from uuid import UUID, uuid4

import httpx
import pytest
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401 -- shared real-service fixture
    database_settings,  # noqa: F401 -- shared isolated database fixture
)

from ledgerpilot.local_setup import FileSettings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1",
    reason="Requires PostgreSQL and Mailpit",
)


async def create_account(
    api: httpx.AsyncClient, base: str, code: str, kind: str, control: str | None = None
) -> str:
    r = await api.post(
        base + "/accounts", json={"code": code, "name": code, "kind": kind, "control": control}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_unauthenticated_upload_does_not_consume_body(client: httpx.AsyncClient) -> None:
    consumed = False

    async def untrusted_body():
        nonlocal consumed
        consumed = True
        yield b"This body must never be buffered before authorization"

    response = await client.post(
        f"/api/v1/businesses/{uuid4()}/evidence/upload",
        params={"filename": "test.csv"},
        content=untrusted_body(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 401, response.text
    assert not consumed


async def test_late_transfer_side_clears_once_and_keeps_both_sources(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        receiving = await create_account(api, base, "receiving", "asset")
        clearing = await create_account(api, base, "clearing", "asset", "clearing")
        first = None
        for aid, day, amount in [
            (a["bank"], "2026-10-01", "-500"),
            (receiving, "2026-10-02", "500"),
        ]:
            data = {
                "account_id": aid,
                "content": f"date,description,amount\n{day},Transfer,{amount}\n",
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                },
            }
            preview = await api.post(base + "/bank-imports/preview", json=data)
            imported = await api.post(
                base + "/bank-imports", json={**data, "expected_hash": preview.json()["file_hash"]}
            )
            assert imported.status_code == 201, imported.text
            row = (await api.get(base + "/bank-transactions", params={"account_id": aid})).json()[
                "items"
            ][0]
            payload = {
                **await command(api, base),
                "expected_row_version": 0,
                "action": "transfer",
                "target_account_id": clearing,
                "allow_clearing": True,
                "reason": "Reviewed late transfer side",
            }
            if first:
                payload["peer_id"] = first["id"]
            result = await api.post(
                base + f"/bank-transactions/{row['id']}/disposition", json=payload
            )
            assert result.status_code == 200, result.text
            if first:
                assert first["journal_id"] != result.json()["journal_id"]
                repeat = await api.post(
                    base + f"/bank-transactions/{row['id']}/disposition", json=payload
                )
                assert repeat.status_code == 200 and repeat.json() == result.json()
            else:
                first = result.json()
        trial = (await api.get(base + "/trial-balance", params={"as_of": "2026-10-31"})).json()
        amounts = {
            r["account_id"]: Decimal(r["debit"]) - Decimal(r["credit"]) for r in trial["items"]
        }
        assert amounts[clearing] == 0
        assert amounts[a["bank"]] + amounts[receiving] == 0


async def setup(api: httpx.AsyncClient, bid: str) -> tuple[str, dict[str, str], str]:
    base = f"/api/v1/businesses/{bid}"
    accounts = {}
    for code, kind, control in [
        ("bank", "asset", None),
        ("income", "income", None),
        ("cost", "expense", None),
        ("ar", "asset", "ar"),
        ("ap", "liability", "ap"),
        ("loan", "liability", "loan_payable"),
        ("interest", "expense", None),
        ("tax", "liability", "tax"),
    ]:
        accounts[code] = await create_account(api, base, code, kind, control)
    result = await api.post(
        base + "/periods", json={"start_date": "2026-01-01", "end_date": "2026-12-31"}
    )
    assert result.status_code == 201, result.text
    party = await api.post(
        base + "/parties",
        json={
            "name": "Synthetic multi-role party",
            "roles": ["customer", "supplier", "lender", "borrower"],
        },
    )
    assert party.status_code == 201, party.text
    return base, accounts, str(party.json()["id"])


async def command(api: httpx.AsyncClient, base: str) -> dict[str, object]:
    version = (await api.get(base)).json()["version"]
    return {"idempotency_key": uuid4().hex, "expected_version": version}


async def test_party_statement_history_and_held_beneficiary_changes(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        issued = await invoice(api, base, a, party)
        assert issued.status_code == 201, issued.text
        for role, expected in [("customer", 100), ("supplier", 0), ("lender", 0)]:
            statement = await api.get(
                base + f"/parties/{party}/statement",
                params={"role": role, "as_of": "2026-10-31", "limit": 1},
            )
            assert statement.status_code == 200, statement.text
            assert Decimal(statement.json()["total"]) == expected
            assert statement.json()["report_id"]
        removed = await api.patch(
            base + f"/parties/{party}",
            json={"name": "Historical party", "roles": ["supplier"], "expected_version": 0},
        )
        assert removed.status_code == 409 and "ERR_PARTY_HISTORY" in removed.text
        data = {
            "idempotency_key": uuid4().hex,
            "expected_party_version": 0,
            "account_name": "Synthetic supplier",
            "bank_name": "Synthetic bank",
            "account_reference": "SYNTHETIC-12345678",
            "reason": "Review before any future change",
        }
        requested = await api.post(base + f"/parties/{party}/beneficiary-changes", json=data)
        assert requested.status_code == 201, requested.text
        assert requested.json()["status"] == "held" and not requested.json()["effective"]
        repeated = await api.post(base + f"/parties/{party}/beneficiary-changes", json=data)
        assert repeated.json()["id"] == requested.json()["id"]
        listed = await api.get(base + "/beneficiary-changes")
        assert listed.status_code == 200 and len(listed.json()["items"]) == 1, listed.text
        assert "SYNTHETIC-12345678" not in listed.text
        assert listed.json()["items"][0]["masked_reference"] == "****5678"
        usage = await api.get(base + "/usage")
        assert usage.status_code == 200 and usage.json()["operations"] == 1, usage.text
        entitlement = await api.get(base + "/entitlement")
        assert entitlement.status_code == 200 and not entitlement.json()["live_charges"]
        replay = await api.post(base + f"/jobs/{uuid4()}/replay", json={"reason": "Test"})
        assert replay.status_code == 409, replay.text
        consent = await api.post(
            base + "/consents",
            json={"purpose": "privacy", "policy_version": "test-v1", "granted": True},
        )
        assert consent.status_code == 201, consent.text
        assert len((await api.get(base + "/consents")).json()["items"]) == 1
        privacy = await api.post(
            base + "/privacy-requests", json={"kind": "deletion", "reason": "Test request"}
        )
        assert privacy.status_code == 201 and not privacy.json()["deleted"], privacy.text
        assert len((await api.get(base + "/privacy-requests")).json()["items"]) == 1
        personal = await api.get(base + "/privacy-export")
        assert personal.status_code == 200, personal.text
        assert personal.json()["privacy_requests"][0]["id"] == privacy.json()["id"]
        assert "password" not in personal.text and "token" not in personal.text


async def invoice(
    api: httpx.AsyncClient, base: str, a: dict[str, str], party: str, kind: str = "invoice"
) -> httpx.Response:
    return await api.post(
        base + "/trade-documents",
        json={
            **await command(api, base),
            "kind": kind,
            "number": uuid4().hex,
            "party_id": party,
            "accounting_date": "2026-10-01",
            "due_date": "2026-11-01",
            "control_account_id": a["ar" if kind == "invoice" else "ap"],
            "lines": [
                {
                    "account_id": a["income" if kind == "invoice" else "cost"],
                    "description": "Synthetic service",
                    "amount": "100",
                }
            ],
        },
    )


async def test_invoice_partial_payment_and_cash_timing(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        issued = await invoice(api, base, a, party)
        assert issued.status_code == 201, issued.text
        did = issued.json()["id"]
        request = {
            **await command(api, base),
            "kind": "receipt",
            "party_id": party,
            "accounting_date": "2026-11-01",
            "bank_account_id": a["bank"],
            "control_account_id": a["ar"],
            "amount": "40",
            "allocations": [{"document_id": did, "amount": "40"}],
        }
        paid = await api.post(base + "/payments", json=request)
        assert paid.status_code == 201, paid.text
        repeat = await api.post(base + "/payments", json=request)
        assert repeat.json() == paid.json()
        detail = await api.get(base + "/trade-documents/" + did, params={"as_of": "2026-11-30"})
        assert detail.status_code == 200, detail.text
        assert detail.json()["outstanding"] == "60.000000"
        for basis, end, expected in [
            ("cash", "2026-10-31", "0"),
            ("cash", "2026-11-30", "40"),
            ("accrual", "2026-10-31", "100"),
        ]:
            result = await api.get(
                base + "/reports/profit-loss",
                params={"start": "2026-01-01", "end": end, "basis": basis},
            )
            assert result.status_code == 200, result.text
            from decimal import Decimal

            assert Decimal(result.json()["income"]) == Decimal(expected)
        tieout = await api.get(base + "/reports/controls", params={"as_of": "2026-11-30"})
        assert tieout.status_code == 200 and tieout.json()["balanced"], tieout.text
        over = await api.post(
            base + "/payments",
            json={
                **request,
                **await command(api, base),
                "amount": "61",
                "allocations": [{"document_id": did, "amount": "61"}],
            },
        )
        assert over.status_code == 409, over.text


async def test_bill_two_payments_and_loan_principal(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        issued = await invoice(api, base, a, party, "bill")
        assert issued.status_code == 201, issued.text
        for amount in ["40", "60"]:
            paid = await api.post(
                base + "/payments",
                json={
                    **await command(api, base),
                    "kind": "payment",
                    "party_id": party,
                    "accounting_date": "2026-11-01",
                    "bank_account_id": a["bank"],
                    "control_account_id": a["ap"],
                    "amount": amount,
                    "allocations": [{"document_id": issued.json()["id"], "amount": amount}],
                },
            )
            assert paid.status_code == 201, paid.text
        loan = await api.post(
            base + "/loans",
            json={
                "party_id": party,
                "direction": "payable",
                "control_account_id": a["loan"],
                "name": "Abdullah loan",
            },
        )
        assert loan.status_code == 201, loan.text
        lid = loan.json()["id"]
        for kind in ["drawdown", "repayment"]:
            movement = await api.post(
                base + f"/loans/{lid}/movements",
                json={
                    **await command(api, base),
                    "kind": kind,
                    "accounting_date": "2026-11-01",
                    "bank_account_id": a["bank"],
                    "principal": "100",
                    "interest": "10" if kind == "repayment" else "0",
                    "interest_account_id": a["interest"],
                },
            )
            assert movement.status_code == 201, movement.text
        tieout = await api.get(base + "/reports/controls", params={"as_of": "2026-11-30"})
        assert tieout.json()["balanced"], tieout.text
        over = await api.post(
            base + f"/loans/{lid}/movements",
            json={
                **await command(api, base),
                "kind": "repayment",
                "accounting_date": "2026-11-02",
                "bank_account_id": a["bank"],
                "principal": "1",
            },
        )
        assert over.status_code == 409, over.text


async def test_bank_reimport_preserves_legitimate_identical_rows(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        data = {
            "account_id": a["bank"],
            "content": "date,description,amount\n2026-10-01,Unknown deposit,3287.64\n"
            "2026-10-01,Unknown deposit,3287.64\n",
            "mapping": {
                "date_column": "date",
                "description_column": "description",
                "amount_column": "amount",
            },
        }
        preview = await api.post(base + "/bank-imports/preview", json=data)
        assert preview.status_code == 200, preview.text
        payload = {**data, "expected_hash": preview.json()["file_hash"]}
        imported = await api.post(base + "/bank-imports", json=payload)
        assert imported.status_code == 201, imported.text
        assert imported.json()["created"] == 2
        repeat = await api.post(base + "/bank-imports", json=payload)
        assert repeat.json()["created"] == 0
        rows = await api.get(base + "/bank-transactions")
        assert len(rows.json()["items"]) == 2
        assert all(
            r["state"] == "for_review" and r["journal_id"] is None for r in rows.json()["items"]
        )
        trial = await api.get(base + "/trial-balance", params={"as_of": "2026-12-31"})
        assert trial.json()["total_debit"] == "0.000000"


async def test_credit_refund_reconciles_and_cannot_refund_twice(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        issued = await invoice(api, base, a, party)
        did = issued.json()["id"]
        paid = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-10-05",
                "bank_account_id": a["bank"],
                "control_account_id": a["ar"],
                "amount": "40",
                "allocations": [{"document_id": did, "amount": "40"}],
            },
        )
        assert paid.status_code == 201, paid.text
        credit = await api.post(
            base + "/trade-documents",
            json={
                **await command(api, base),
                "kind": "customer_credit",
                "number": uuid4().hex,
                "party_id": party,
                "accounting_date": "2026-10-06",
                "due_date": "2026-10-06",
                "original_id": did,
                "control_account_id": a["ar"],
                "lines": [
                    {"account_id": a["income"], "description": "Credit service", "amount": "100"}
                ],
            },
        )
        assert credit.status_code == 201, credit.text
        request = {
            "kind": "customer_refund",
            "party_id": party,
            "accounting_date": "2026-10-07",
            "bank_account_id": a["bank"],
            "control_account_id": a["ar"],
            "amount": "40",
            "original_payment_id": paid.json()["id"],
            "allocations": [
                {"document_id": did, "credit_note_id": credit.json()["id"], "amount": "40"}
            ],
        }
        refunded = await api.post(base + "/payments", json={**request, **await command(api, base)})
        assert refunded.status_code == 201, refunded.text
        again = await api.post(base + "/payments", json={**request, **await command(api, base)})
        assert again.status_code == 409, again.text
        doc = await api.get(base + f"/trade-documents/{did}", params={"as_of": "2026-10-31"})
        assert Decimal(doc.json()["outstanding"]) == 0
        for basis in ["cash", "accrual"]:
            report = await api.get(
                base + "/reports/profit-loss",
                params={"start": "2026-10-01", "end": "2026-10-31", "basis": basis},
            )
            assert report.status_code == 200, report.text
            assert Decimal(report.json()["net_profit"]) == 0
            cited = await api.post(
                base + "/assistant/report-answer",
                json={"report_id": report.json()["report_id"], "metric": "net_profit"},
            )
            assert cited.status_code == 200 and Decimal(cited.json()["value"]) == 0, cited.text
        tieout = await api.get(base + "/reports/controls", params={"as_of": "2026-10-31"})
        assert tieout.json()["balanced"], tieout.text


async def test_advance_application_and_reminder_settlement_recheck(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        deposit = await create_account(api, base, "customer-deposits", "liability")
        advance = await api.post(
            base + "/advances",
            json={
                **await command(api, base),
                "party_id": party,
                "direction": "customer",
                "accounting_date": "2026-10-01",
                "deposit_account_id": deposit,
                "bank_account_id": a["bank"],
                "amount": "100",
            },
        )
        assert advance.status_code == 201, advance.text
        issued = await invoice(api, base, a, party)
        assert issued.status_code == 201, issued.text
        applied = await api.post(
            base + f"/advances/{advance.json()['id']}/applications",
            json={
                **await command(api, base),
                "document_id": issued.json()["id"],
                "accounting_date": "2026-10-02",
                "amount": "100",
            },
        )
        assert applied.status_code == 201, applied.text
        aging = await api.get(base + "/reports/aging", params={"as_of": "2026-11-30"})
        assert Decimal(aging.json()["total"]) == 0
        reminder = await api.post(base + "/reminders/review", json={"as_of": "2026-11-30"})
        assert reminder.status_code == 200 and reminder.json()["created"] == 0, reminder.text
        preference = await api.patch(
            base + "/notification-preferences",
            json={"expected_version": 0, "marketing": False, "reminders": False},
        )
        assert preference.status_code == 200 and preference.json()["version"] == 1, preference.text
        stale = await api.patch(base + "/notification-preferences", json={"expected_version": 0})
        assert stale.status_code == 409


async def test_bank_rule_requires_fresh_preview_and_never_posts(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        rule = await api.post(
            base + "/bank-rules",
            json={"name": "Reviewed category", "contains": "payment", "account_id": a["income"]},
        )
        assert rule.status_code == 201, rule.text
        rid = rule.json()["id"]
        no_preview = await api.post(
            base + f"/bank-rules/{rid}/activation", json={"expected_version": 0, "active": True}
        )
        assert no_preview.status_code == 409
        preview = await api.get(base + f"/bank-rules/{rid}/preview")
        activate = await api.post(
            base + f"/bank-rules/{rid}/activation",
            json={
                "expected_version": 0,
                "active": True,
                "preview_hash": preview.json()["preview_hash"],
            },
        )
        assert activate.status_code == 200 and activate.json()["version"] == 1, activate.text
        rollback = await api.post(
            base + f"/bank-rules/{rid}/rollback", json={"expected_version": 1, "restore_version": 0}
        )
        assert rollback.status_code == 200 and rollback.json()["active"] is False, rollback.text


async def test_close_requires_independent_reviewer_and_membership_changes_are_scoped(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        owner_id = (await client.get("/api/v1/auth/me")).json()["id"]
        members = await client.get(base + "/memberships")
        assert members.status_code == 200 and len(members.json()["items"]) == 2, members.text
        period = (await api.get(base + "/periods")).json()["items"][0]["id"]
        task = await client.post(
            base + "/close-tasks",
            json={"period_id": period, "name": "Review control tie-out", "assigned_to": uid},
        )
        assert task.status_code == 201, task.text
        tid = task.json()["id"]
        lock = await api.patch(
            base + f"/periods/{period}", json={"locked": True, "expected_version": 0}
        )
        assert lock.status_code == 409, lock.text
        prepared = await api.patch(
            base + f"/close-tasks/{tid}",
            json={
                "expected_version": 0,
                "state": "prepared",
                "comment": "Prepared synthetic evidence",
            },
        )
        assert prepared.status_code == 200, prepared.text
        self_approve = await api.patch(
            base + f"/close-tasks/{tid}", json={"expected_version": 1, "state": "approved"}
        )
        assert self_approve.status_code == 403
        approved = await client.patch(
            base + f"/close-tasks/{tid}", json={"expected_version": 1, "state": "approved"}
        )
        assert approved.status_code == 200, approved.text
        final_owner = await client.patch(
            base + f"/memberships/{owner_id}",
            json={"expected_version": 0, "role": "auditor", "active": False},
        )
        assert final_owner.status_code == 409, final_owner.text
        removed = await client.patch(
            base + f"/memberships/{uid}",
            json={"expected_version": 0, "role": "accountant", "active": False},
        )
        assert removed.status_code == 200, removed.text
        assert (await api.get(base + "/parties")).status_code == 404


async def test_evidence_queue_remains_quarantined_when_scanner_is_unavailable(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    import asyncio
    from pathlib import Path

    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from ledgerpilot.documents.worker import run_once

    settings = database_settings.model_copy(
        update={
            "clamav_port": 1,
            "evidence_encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    async with accountant_context(client, settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        upload = await api.post(
            base + "/evidence/upload",
            params={"filename": "statement.csv"},
            content=b"date,amount\n2026-10-01,100\n",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201, upload.text
        eid = upload.json()["id"]
        target = Path(settings.evidence_storage_root) / bid / (eid + ".bin")
        try:
            assert upload.json()["scan_status"] == "quarantined"
            assert target.exists() and b"date,amount" not in target.read_bytes()
            blocked = await api.get(base + f"/evidence/{eid}/download")
            assert blocked.status_code == 409
            outcome = await asyncio.to_thread(run_once, settings, UUID(uid), UUID(bid))
            assert outcome["processed"] and outcome["completion_accepted"], outcome
            assert outcome["error_code"] == "ERR_SCAN_UNAVAILABLE"
            jobs = await api.get(base + "/jobs")
            assert jobs.status_code == 200 and jobs.json()["items"][0]["attempts"] == 1, jobs.text
            assert jobs.json()["items"][0]["state"] == "queued"
            assert "lease_token" not in jobs.text
            assert (await api.get(base + f"/evidence/{eid}/download")).status_code == 409
        finally:
            target.unlink(missing_ok=True)


async def test_source_drafts_are_unposted_until_issued_and_voids_never_post(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        payload = {
            **await command(api, base),
            "kind": "invoice",
            "number": uuid4().hex,
            "party_id": party,
            "accounting_date": "2026-10-01",
            "due_date": "2026-11-01",
            "control_account_id": a["ar"],
            "lines": [
                {
                    "account_id": a["income"],
                    "description": "Draft synthetic service",
                    "amount": "100",
                }
            ],
        }
        draft = await client.post(base + "/source-drafts", json=payload)
        assert draft.status_code == 201, draft.text
        did = draft.json()["id"]
        preview = await client.post(base + "/trade-documents/preview", json=payload)
        assert preview.status_code == 200 and preview.json()["posted"] is False, preview.text
        empty = await api.get(base + "/trade-documents", params={"as_of": "2026-12-31"})
        assert empty.json()["items"] == []
        stale = await client.patch(
            base + f"/source-drafts/{did}", json={"expected_version": 1, "payload": payload}
        )
        assert stale.status_code == 409
        posted = await api.post(
            base + f"/source-drafts/{did}/issue",
            json={"expected_version": 0, "expected_business_version": 0},
        )
        assert posted.status_code == 200, posted.text
        request = {**payload, "idempotency_key": uuid4().hex, "number": uuid4().hex}
        void = await client.post(base + "/source-drafts", json=request)
        assert void.status_code == 201, void.text
        void_id = void.json()["id"]
        result = await client.post(
            base + f"/source-drafts/{void_id}/void",
            json={"expected_version": 0, "expected_business_version": 1},
        )
        assert result.status_code == 200, result.text
        assert (
            await api.post(
                base + f"/source-drafts/{void_id}/issue",
                json={"expected_version": 1, "expected_business_version": 1},
            )
        ).status_code == 409


async def test_bank_split_match_and_two_sided_transfer_do_not_duplicate_profit(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, _ = await setup(api, bid)
        second = await create_account(api, base, "bank-two", "asset")

        async def bank_import(aid: str, content: str) -> list[dict[str, object]]:
            data = {
                "account_id": aid,
                "content": content,
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                },
            }
            preview = await api.post(base + "/bank-imports/preview", json=data)
            imported = await api.post(
                base + "/bank-imports", json={**data, "expected_hash": preview.json()["file_hash"]}
            )
            assert imported.status_code == 201, imported.text
            rows = await api.get(base + "/bank-transactions", params={"account_id": aid})
            return rows.json()["items"]

        first = await bank_import(a["bank"], "date,description,amount\n2026-10-01,Transfer,-500\n")
        peer = await bank_import(second, "date,description,amount\n2026-10-01,Transfer,500\n")
        result = await api.post(
            base + f"/bank-transactions/{first[0]['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "transfer",
                "reason": "Approved synthetic internal transfer",
                "target_account_id": second,
                "peer_id": peer[0]["id"],
            },
        )
        assert result.status_code == 200, result.text
        profit = await api.get(
            base + "/reports/profit-loss", params={"start": "2026-10-01", "end": "2026-10-31"}
        )
        assert Decimal(profit.json()["net_profit"]) == 0
        again = await api.post(
            base + f"/bank-transactions/{peer[0]['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "exclude",
                "reason": "Must not consume transfer twice",
            },
        )
        assert again.status_code == 409
        spend = await bank_import(a["bank"], "date,description,amount\n2026-10-02,Cost,-100\n")
        split = await api.post(
            base + f"/bank-transactions/{spend[0]['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "split",
                "reason": "Reviewed cost and fee",
                "splits": [
                    {"account_id": a["cost"], "amount": "90"},
                    {"account_id": a["interest"], "amount": "10"},
                ],
            },
        )
        assert split.status_code == 200, split.text
        journal = (await api.get(base + "/journals/" + split.json()["journal_id"])).json()
        op = await api.get(base + "/operations/" + journal["source_id"])
        assert op.status_code == 200, op.text
        blocked = await api.post(
            base + f"/journals/{split.json()['journal_id']}/reversal",
            json={
                **await command(api, base),
                "accounting_date": "2026-10-03",
                "memo": "Reject unlinked correction",
            },
        )
        assert blocked.status_code == 409, blocked.text


@pytest.mark.skipif(
    os.environ.get("RUN_DOCUMENT_SCANNER_TESTS") != "1",
    reason="Requires the real local ClamAV scanner",
)
async def test_real_scanner_statement_and_signed_offline_export(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    import asyncio
    import hashlib
    import io
    import json
    from pathlib import Path
    from zipfile import ZipFile

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from ledgerpilot.documents.worker import run_once

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        source = await invoice(api, base, a, party)
        assert source.status_code == 201, source.text
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        original = b"date,amount\n2026-10-31,0\n"
        upload = await api.post(
            base + "/evidence/upload",
            params={"filename": "statement.csv"},
            content=original,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201, upload.text
        eid = upload.json()["id"]
        target = Path(database_settings.evidence_storage_root) / bid / (eid + ".bin")
        try:
            outcome = await asyncio.to_thread(run_once, database_settings, UUID(uid), UUID(bid))
            assert outcome["outcome"] == "unsupported" and outcome["completion_accepted"], outcome
            downloaded = await api.get(base + f"/evidence/{eid}/download")
            assert downloaded.status_code == 200 and downloaded.content == original
            bad = await api.post(
                base + "/statements/sign-off",
                json={
                    **await command(api, base),
                    "account_id": a["bank"],
                    "as_of": "2026-10-31",
                    "closing_balance": "1",
                    "evidence_id": eid,
                },
            )
            assert bad.status_code == 409, bad.text
            good = await api.post(
                base + "/statements/sign-off",
                json={
                    **await command(api, base),
                    "account_id": a["bank"],
                    "as_of": "2026-10-31",
                    "closing_balance": "0",
                    "evidence_id": eid,
                },
            )
            assert good.status_code == 201, good.text
            export = await api.get(
                base + "/exports/evidence-pack.zip",
                params={"start": "2026-10-01", "end": "2026-10-31"},
            )
            assert export.status_code == 200, export.text if export.status_code != 200 else ""
            trusted = (await api.get(base + "/exports/verification-key")).json()
            with ZipFile(io.BytesIO(export.content)) as archive:
                manifest_bytes = archive.read("manifest.json")
                manifest = json.loads(manifest_bytes)
                key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted["public_key_hex"]))
                key.verify(archive.read("manifest.sig"), manifest_bytes)
                assert manifest["verification_key"] == trusted
                for name, info in manifest["files"].items():
                    assert not name.startswith(("/", ".."))
                    assert hashlib.sha256(archive.read(name)).hexdigest() == info["sha256"]
                assert archive.read(f"evidence/{eid}.csv") == original
        finally:
            target.unlink(missing_ok=True)


def synthetic_pdf() -> bytes:
    """A real one-page PDF with a known synthetic amount and ISO date."""
    text = b"BT /F1 28 Tf 72 700 Td (INVOICE 100.00 2026-10-01) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(result))
        result += str(index).encode() + b" 0 obj\n" + value + b"\nendobj\n"
    start = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    result += (
        b"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n"
    )
    return bytes(result)


@pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_OCR_TESTS") != "1",
    reason="Requires real ClamAV, OCR container and Docker fixture rendering",
)
async def test_real_pdf_text_and_scanned_png_extraction_remain_unposted(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    import asyncio
    import subprocess
    from pathlib import Path

    from ledgerpilot.documents.worker import run_once

    settings = database_settings.model_copy(update={"ocr_enabled": True})
    # Render the known PDF fixture to genuine pixels with the installed Poppler tool.
    render = (
        "import sys,tempfile,subprocess,pathlib; "
        "d=tempfile.TemporaryDirectory(); p=pathlib.Path(d.name); "
        "(p/'fixture.pdf').write_bytes(sys.stdin.buffer.read()); "
        "subprocess.run(['pdftoppm','-singlefile','-scale-to','2000','-png',"
        "str(p/'fixture.pdf'),str(p/'page')],check=True); "
        "sys.stdout.buffer.write((p/'page.png').read_bytes()); d.cleanup()"
    )
    png = await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "exec",
            "-i",
            "ledgerpilot-documents-ocr-1",
            "/app/.venv/bin/python",
            "-c",
            render,
        ],
        input=synthetic_pdf(),
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert png.stdout.startswith(b"\x89PNG")
    async with accountant_context(client, settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        for filename, content, expected_engine in [
            ("invoice.pdf", synthetic_pdf(), "embedded-text"),
            ("invoice.png", png.stdout, "tesseract"),
        ]:
            uploaded = await api.post(
                base + "/evidence/upload",
                params={"filename": filename},
                content=content,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert uploaded.status_code == 201, uploaded.text
            eid = uploaded.json()["id"]
            target = Path(settings.evidence_storage_root) / bid / (eid + ".bin")
            try:
                result = await asyncio.to_thread(run_once, settings, UUID(uid), UUID(bid))
                assert result["completion_accepted"] and result["outcome"] == "extracted", result
                details = await api.get(base + f"/evidence/{eid}")
                assert details.status_code == 200, details.text
                value = details.json()
                assert "INVOICE" in value["raw_text"].upper()
                extracted = value["extraction"]
                assert expected_engine in extracted["model_version"].lower()
                assert extracted["requires_review"] is True
                assert any(
                    f["value_type"] == "amount" and f["value"] == "100.00"
                    for f in extracted["fields"]
                )
                assert all(
                    0 <= f["left"] < f["right"] <= 1
                    and 0 <= f["top"] < f["bottom"] <= 1
                    and f["semantic_role"] == "unassigned"
                    for f in extracted["fields"]
                )
                if expected_engine == "embedded-text":
                    assert all(f["confidence"] is None for f in extracted["fields"])
                else:
                    assert all(0 <= f["confidence"] <= 1 for f in extracted["fields"])
                trial = await api.get(base + "/trial-balance", params={"as_of": "2026-12-31"})
                assert trial.json()["total_debit"] == "0.000000"
            finally:
                target.unlink(missing_ok=True)


async def test_receipt_fee_closes_ar_and_tax_remittance_is_not_an_expense(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        source = await api.post(
            base + "/trade-documents",
            json={
                **await command(api, base),
                "kind": "invoice",
                "number": uuid4().hex,
                "party_id": party,
                "accounting_date": "2026-10-01",
                "due_date": "2026-10-31",
                "control_account_id": a["ar"],
                "lines": [
                    {
                        "account_id": a["income"],
                        "description": "Reviewed taxable source",
                        "amount": "100",
                        "tax_amount": "10",
                        "tax_account_id": a["tax"],
                        "tax_code": "manually-reviewed-test-policy",
                    }
                ],
            },
        )
        assert source.status_code == 201, source.text
        payment = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-10-02",
                "bank_account_id": a["bank"],
                "control_account_id": a["ar"],
                "amount": "110",
                "fee_amount": "3",
                "fee_account_id": a["cost"],
                "allocations": [{"document_id": source.json()["id"], "amount": "110"}],
            },
        )
        assert payment.status_code == 201, payment.text
        remittance = await api.post(
            base + "/tax/remittances",
            json={
                **await command(api, base),
                "accounting_date": "2026-10-03",
                "bank_account_id": a["bank"],
                "tax_account_id": a["tax"],
                "amount": "10",
            },
        )
        assert remittance.status_code == 201, remittance.text
        report = await api.get(
            base + "/reports/profit-loss", params={"start": "2026-10-01", "end": "2026-10-31"}
        )
        assert Decimal(report.json()["income"]) == 100 and Decimal(report.json()["expense"]) == 3
        tax = await api.get(
            base + "/reports/tax-support", params={"start": "2026-10-01", "end": "2026-10-31"}
        )
        assert tax.status_code == 200 and Decimal(tax.json()["items"][0]["difference"]) == 0, (
            tax.text
        )


async def test_csv_import_is_reviewed_atomic_idempotent_and_compares_with_baseline(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, _, party = await setup(api, bid)
        text = (
            "kind,party_id,accounting_date,due_date,number,control_account_code,category_account_code,amount,bank_account_code,document_number\n"
            f"invoice,{party},2026-10-01,2026-11-01,LEGACY-1,ar,income,100,,\n"
            f"receipt,{party},2026-11-01,,,ar,,40,bank,LEGACY-1\n"
        )
        data = {
            "mode": "trade",
            "content": text,
            "cutover_date": "2026-11-30",
            "expected_trial": {"bank": "40", "ar": "60", "income": "-100"},
        }
        preview = await api.post(base + "/imports/preview", json=data)
        assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
        empty = await api.get(base + "/trade-documents", params={"as_of": "2026-11-30"})
        assert empty.json()["items"] == []
        payload = {
            **data,
            **await command(api, base),
            "preview_hash": preview.json()["preview_hash"],
        }
        applied = await api.post(base + "/imports/apply", json=payload)
        assert applied.status_code == 201 and applied.json()["applied"] == 2, applied.text
        replay = await api.post(base + "/imports/apply", json=payload)
        assert replay.json() == applied.json(), replay.text
        comparison = await api.get(base + f"/imports/{applied.json()['id']}/comparison")
        assert comparison.status_code == 200 and comparison.json()["balanced"], comparison.text
        assert comparison.json()["controls"]["balanced"]
        duplicate = await api.post(
            base + "/imports/apply", json={**payload, **await command(api, base)}
        )
        assert duplicate.status_code == 409
        bad = {
            **data,
            "content": text.replace("LEGACY-1", "LEGACY-BAD"),
            "expected_trial": {"bank": "80", "ar": "999", "income": "-200"},
        }
        review = await api.post(base + "/imports/preview", json=bad)
        failed = await api.post(
            base + "/imports/apply",
            json={**bad, **await command(api, base), "preview_hash": review.json()["preview_hash"]},
        )
        assert failed.status_code == 409, failed.text
        final = await api.get(base + "/trade-documents", params={"as_of": "2026-11-30"})
        assert len(final.json()["items"]) == 1
        assert (await api.get(base)).json()["version"] == 2


async def test_many_to_one_bank_match_and_reclassification_preserve_source_totals(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    from decimal import Decimal

    async with accountant_context(client, database_settings) as (bid, api):
        base, a, party = await setup(api, bid)
        deposit = await create_account(api, base, "deposit", "liability")
        issued = await invoice(api, base, a, party)
        paid = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-10-05",
                "bank_account_id": a["bank"],
                "control_account_id": a["ar"],
                "amount": "100",
                "allocations": [{"document_id": issued.json()["id"], "amount": "100"}],
            },
        )
        advance = await api.post(
            base + "/advances",
            json={
                **await command(api, base),
                "party_id": party,
                "direction": "customer",
                "accounting_date": "2026-10-05",
                "deposit_account_id": deposit,
                "bank_account_id": a["bank"],
                "amount": "20",
            },
        )
        assert paid.status_code == 201 and advance.status_code == 201
        data = {
            "account_id": a["bank"],
            "content": (
                "date,description,amount\n2026-10-05,Bundle,120\n2026-10-06,Review cost,-50\n"
            ),
            "mapping": {
                "date_column": "date",
                "description_column": "description",
                "amount_column": "amount",
            },
        }
        preview = await api.post(base + "/bank-imports/preview", json=data)
        imported = await api.post(
            base + "/bank-imports", json={**data, "expected_hash": preview.json()["file_hash"]}
        )
        assert imported.status_code == 201
        rows = (await api.get(base + "/bank-transactions")).json()["items"]
        bundle = next(r for r in rows if r["description"] == "Bundle")
        cost = next(r for r in rows if r["description"] == "Review cost")
        request = {
            **await command(api, base),
            "expected_row_version": 0,
            "action": "match",
            "reason": "Reviewed receipt plus unallocated advance",
            "journal_ids": [paid.json()["journal_id"], advance.json()["journal_id"]],
        }
        review = await client.post(
            base + f"/bank-transactions/{bundle['id']}/preview", json=request
        )
        assert review.status_code == 200 and review.json()["posted"] is False, review.text
        match = await api.post(
            base + f"/bank-transactions/{bundle['id']}/disposition", json=request
        )
        assert match.status_code == 200 and match.json()["state"] == "matched", match.text
        matches = await api.get(base + f"/bank-transactions/{bundle['id']}/matches")
        assert len(matches.json()["items"]) == 2
        classified = await api.post(
            base + f"/bank-transactions/{cost['id']}/disposition",
            json={
                **await command(api, base),
                "expected_row_version": 0,
                "action": "categorize",
                "reason": "Initial review",
                "splits": [{"account_id": a["cost"], "amount": "50"}],
            },
        )
        assert classified.status_code == 200, classified.text
        corrected = await api.post(
            base + f"/bank-transactions/{cost['id']}/reclassifications",
            json={
                **await command(api, base),
                "accounting_date": "2026-10-07",
                "reason": "Corrected expense category",
                "splits": [{"account_id": a["interest"], "amount": "50"}],
            },
        )
        assert corrected.status_code == 201 and not corrected.json()["bank_movement_changed"], (
            corrected.text
        )
        trial = await api.get(base + "/trial-balance", params={"as_of": "2026-10-31"})
        by_id = {r["account_id"]: r for r in trial.json()["items"]}
        assert Decimal(by_id[a["bank"]]["debit"]) == 70
        assert Decimal(by_id[a["cost"]]["debit"]) == 0
        assert Decimal(by_id[a["interest"]]["debit"]) == 50
