"""Real PostgreSQL query budgets, rollback and tenant-scope regressions."""

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import Engine, event, select, text
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
    journal_request,
)
from test_phase3_integration import command, create_account, invoice, setup

from ledgerpilot.accounting.models import Account
from ledgerpilot.database import set_context
from ledgerpilot.documents.worker import run_once
from ledgerpilot.identity.dependencies import ACCESS_COOKIE, DB, Identity
from ledgerpilot.identity.security import decode_access
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.reports import service as reports
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade.schemas import DocumentCreate
from ledgerpilot.trade.service import document_impact
from ledgerpilot.workspace.imports import ImportPreview, compile_rows

# ruff: noqa: F811 -- imported isolated PostgreSQL/SMTP fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL/Mailpit/ClamAV"
)
METRICS: dict[str, Any] = {}


@pytest.fixture(scope="module", autouse=True)
def write_query_evidence():
    yield
    target = Path(__file__).resolve().parents[2] / "tmp/database-audit/query-counts.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(METRICS, indent=2) + "\n", encoding="utf-8")


@contextmanager
def select_count(engine: Engine) -> Iterator[list[str]]:
    """Count statements without retaining bindings, credentials or financial values."""
    statements: list[str] = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)


async def test_document_lines_and_import_preview_have_constant_query_counts(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        app = api._transport.app
        counts = []
        preview_counts = []
        for size in [1, 60]:
            with app.state.sessions.begin() as db:
                set_context(db, uid, UUID(bid))
                business = get_business(db, UUID(bid))
                data = DocumentCreate(
                    idempotency_key=uuid4().hex,
                    expected_version=business.version,
                    kind="invoice",
                    number=uuid4().hex,
                    party_id=UUID(party),
                    accounting_date=date(2026, 10, 1),
                    due_date=date(2026, 11, 1),
                    control_account_id=UUID(accounts["ar"]),
                    lines=[
                        {
                            "account_id": accounts["income"],
                            "description": "Service",
                            "amount": "10",
                            "tax_amount": "1",
                            "tax_account_id": accounts["tax"],
                            "tax_code": "reviewed",
                        }
                    ]
                    * size,
                )
                with select_count(app.state.engine) as queries:
                    _, total, tax = document_impact(db, business, data)
                assert total == size * 11 and tax == size
                counts.append(len(queries))
                preview_size = min(size, 25)
                csv = (
                    "kind,party_id,accounting_date,control_account_code,category_account_code,number,due_date,amount\n"
                    + "".join(
                        f"invoice,{party},2026-10-01,ar,income,SOURCE-{i},2026-11-01,10\n"
                        for i in range(preview_size)
                    )
                )
                with select_count(app.state.engine) as queries:
                    commands, errors = compile_rows(
                        db,
                        UUID(bid),
                        ImportPreview(
                            mode="trade",
                            content=csv,
                            cutover_date=date(2026, 10, 31),
                            expected_trial={
                                "ar": str(preview_size * 10),
                                "income": str(-preview_size * 10),
                            },
                        ),
                    )
                assert not errors and len(commands) == preview_size
                preview_counts.append(len(queries))
        assert counts == [2, 2]
        assert preview_counts[0] == preview_counts[1] == 3
        METRICS["document_lines_1_60"] = counts
        METRICS["preview_rows_1_25"] = preview_counts
        # Batch lookups retain the same not-found behavior for unshared tenant IDs.
        with app.state.sessions.begin() as db:
            set_context(db, uid, UUID(bid))
            with pytest.raises(DomainError, match="ERR_ACCOUNT"):
                document_impact(
                    db,
                    get_business(db, UUID(bid)),
                    data.model_copy(update={"control_account_id": uuid4()}),
                )


async def test_bank_import_external_ids_are_loaded_once_and_conflicts_roll_back(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, _ = await setup(api, bid)
        counts = []
        for size in [1, 100]:
            payload = {
                "account_id": accounts["bank"],
                "content": "date,description,amount,id\n"
                + "".join(f"2026-10-01,Sale,10,{size}-{i}\n" for i in range(size)),
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                    "external_id_column": "id",
                },
            }
            preview = await api.post(base + "/bank-imports/preview", json=payload)
            assert preview.status_code == 200, preview.text
            with select_count(api._transport.app.state.engine) as queries:
                reply = await api.post(
                    base + "/bank-imports",
                    json={**payload, "expected_hash": preview.json()["file_hash"]},
                )
            assert reply.status_code == 201 and reply.json()["created"] == size, reply.text
            counts.append(len(queries))
        assert counts[0] == counts[1]
        METRICS["bank_import_rows_1_100"] = counts
        # A new row queued before a conflicting source must also roll back.
        payload["content"] = (
            "date,description,amount,id\n2026-10-01,New,10,new-before-conflict\n2026-10-01,Changed,20,100-0\n"
        )
        preview = await api.post(base + "/bank-imports/preview", json=payload)
        reply = await api.post(
            base + "/bank-imports", json={**payload, "expected_hash": preview.json()["file_hash"]}
        )
        assert reply.status_code == 409 and reply.json()["error"]["code"] == "ERR_SOURCE_CONFLICT"
        rows = await api.get(
            base + "/bank-transactions", params={"account_id": accounts["bank"], "limit": 200}
        )
        assert len(rows.json()["items"]) == 101


@pytest.mark.parametrize("basis", ["accrual", "cash"])
async def test_dashboard_months_and_cash_accounts_do_not_increase_query_count(
    client: httpx.AsyncClient, database_settings: FileSettings, basis: str
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        source = await invoice(api, base, accounts, party)
        assert source.status_code == 201, source.text
        result = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-11-01",
                "bank_account_id": accounts["bank"],
                "control_account_id": accounts["ar"],
                "amount": "40",
                "allocations": [{"document_id": source.json()["id"], "amount": "40"}],
            },
        )
        assert result.status_code == 201, result.text
        extra = [await create_account(api, base, "cash-" + str(i), "asset") for i in range(4)]
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        app = api._transport.app
        counts = []
        for start, aids in [
            (date(2026, 11, 1), [accounts["bank"]]),
            (date(2026, 1, 1), [accounts["bank"], *extra]),
        ]:
            with app.state.sessions.begin() as db:
                set_context(db, uid, UUID(bid))
                with select_count(app.state.engine) as queries:
                    result = reports.dashboard(
                        db, UUID(bid), start, date(2026, 12, 31), basis, [UUID(a) for a in aids]
                    )
                counts.append(len(queries))
                assert Decimal(result["cash_balance"]) == 40
                assert sum(
                    (Decimal(row["net_profit"]) for row in result["trend"]), Decimal(0)
                ) == Decimal(result["net_profit"])
                assert Decimal(result["receivables"]["total"]) == 60
                assert result["controls"]["balanced"]
                assert Decimal(result["net_profit"]) == (
                    40 if basis == "cash" else 0 if start.month == 11 else 100
                )
        assert counts[0] == counts[1]
        METRICS[f"dashboard_{basis}_2_12_months_1_5_accounts"] = counts


async def test_payment_allocation_lookup_is_constant_and_all_balances_settle(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        sources = []
        for _ in range(8):
            reply = await invoice(api, base, accounts, party)
            assert reply.status_code == 201, reply.text
            sources.append(reply.json()["id"])
        counts = []
        for ids in [sources[:1], sources[1:]]:
            payload = {
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-10-15",
                "bank_account_id": accounts["bank"],
                "control_account_id": accounts["ar"],
                "amount": str(len(ids) * 100),
                "allocations": [{"document_id": did, "amount": "100"} for did in ids],
            }
            with select_count(api._transport.app.state.engine) as queries:
                result = await api.post(base + "/payments", json=payload)
            assert result.status_code == 201, result.text
            counts.append(len(queries))
        assert counts[0] == counts[1]
        METRICS["payment_allocations_1_7"] = counts
        result = await api.get(base + "/reports/controls", params={"as_of": "2026-10-31"})
        assert result.status_code == 200 and result.json()["balanced"], result.text


@pytest.mark.parametrize("failure,status", [("constraint", 409), ("commit", 409), ("timeout", 503)])
async def test_database_failures_roll_back_and_never_expose_sql_or_bindings(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
    failure: str,
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, _, _ = await setup(api, bid)
        app = api._transport.app
        marker = "rollback-" + uuid4().hex[:20]
        confidential = "private-error-detail-" + uuid4().hex

        @app.post("/api/v1/_test/database-failure/{business_id}")
        def fail(business_id: UUID, identity: Identity, db: DB) -> dict[str, str]:
            authorize(db, identity, business_id)
            db.add(Account(business_id=business_id, code=marker, name=confidential, kind="asset"))
            db.flush()
            if failure == "timeout":
                db.execute(text("SELECT set_config('statement_timeout','30',true)"))
                db.execute(text("SELECT pg_sleep(0.3), :private"), {"private": confidential})
            else:
                db.add(
                    Account(business_id=business_id, code="bank", name=confidential, kind="asset")
                )
                if failure == "constraint":
                    db.flush()
                # 'commit' fails in dependency teardown after the handler returns.
            return {"status": "must-not-escape-on-failure"}

        logger = logging.getLogger("ledgerpilot.errors")
        # Alembic's in-process fixture configuration disables existing loggers;
        # explicitly enable this target so redaction is actually exercised.
        was_disabled = logger.disabled
        logger.disabled = False
        logger.addHandler(caplog.handler)
        try:
            result = await api.post(f"/api/v1/_test/database-failure/{bid}")
        finally:
            logger.removeHandler(caplog.handler)
            logger.disabled = was_disabled
        assert confidential not in caplog.text
        assert ('"sqlstate": "23505"' if status == 409 else '"sqlstate": "57014"') in caplog.text
        assert result.status_code == status, result.text
        assert result.json()["error"]["code"] == (
            "ERR_CONFLICT" if status == 409 else "ERR_DEPENDENCY"
        )
        assert (
            confidential not in result.text
            and "INSERT" not in result.text
            and "pg_sleep" not in result.text
        )
        assert "must-not-escape" not in result.text
        # A fresh pooled transaction works and cannot see the partially flushed marker.
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        with app.state.sessions.begin() as db:
            set_context(db, uid, UUID(bid))
            assert (
                db.scalar(
                    select(Account.id).where(
                        Account.business_id == UUID(bid), Account.code == marker
                    )
                )
                is None
            )
        assert (await api.get(base)).status_code == 200


async def test_worker_database_outage_is_sanitized(database_settings: FileSettings) -> None:
    # Closed local port, bounded real driver connection failure; no substitute DB.
    from pydantic import SecretStr
    from sqlalchemy.engine import make_url

    url = make_url(database_settings.database_url.get_secret_value()).set(port=1)
    settings = database_settings.model_copy(
        update={
            "database_url": SecretStr(url.render_as_string(hide_password=False)),
            "db_connect_timeout_seconds": 1,
        }
    )
    with pytest.raises(DomainError) as raised:
        await asyncio.to_thread(run_once, settings, uuid4(), uuid4())
    assert raised.value.code == "ERR_DEPENDENCY" and raised.value.status == 503
    assert "password" not in raised.value.message.lower() and str(raised.value) == "ERR_DEPENDENCY"


async def test_reconciliation_batches_items_and_rejects_unknown_sources(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, _ = await setup(api, bid)
        # Use the actual upload, scanner and leased worker, retaining no test file.
        content = b"bank,date,balance\nSynthetic,2026-10-31,0\n"
        upload = await api.post(
            base + "/evidence/upload",
            params={"filename": "statement.csv"},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 201, upload.text
        eid = upload.json()["id"]
        uid, _ = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
        original = Path(database_settings.evidence_storage_root) / bid / (eid + ".bin")
        try:
            outcome = await asyncio.to_thread(run_once, database_settings, uid, UUID(bid))
            assert outcome["completion_accepted"], outcome
            journals = []
            for _ in range(6):
                request = {
                    **journal_request(accounts["bank"], accounts["income"]),
                    **await command(api, base),
                }
                result = await api.post(base + "/journals", json=request)
                assert result.status_code == 201, result.text
                journals.append(result.json()["id"])
            counts = []
            for size in [1, 6]:
                request = {
                    **await command(api, base),
                    "account_id": accounts["bank"],
                    "as_of": "2026-10-31",
                    "closing_balance": str((6 - size) * 100),
                    "evidence_id": eid,
                    "outstanding": [{"journal_id": jid, "position": 1} for jid in journals[:size]],
                }
                # Statement date is unique. Evaluate the first candidate in a rolled-back
                # transaction, then exercise the six-item candidate through HTTP.
                if size == 1:
                    from ledgerpilot.banking.reconciliation import sign_off
                    from ledgerpilot.banking.schemas import StatementCreate
                    from ledgerpilot.identity import repository
                    from ledgerpilot.identity.dependencies import Principal
                    from ledgerpilot.identity.service import valid_family

                    app = api._transport.app
                    _, sid = decode_access(database_settings, str(api.cookies.get(ACCESS_COOKIE)))
                    with app.state.sessions() as db:
                        set_context(db, uid, UUID(bid))
                        family = repository.session_record(db, uid, sid)
                        assert family is not None
                        who = Principal(valid_family(db, family), family)
                        with select_count(app.state.engine) as queries:
                            result = sign_off(
                                db, who, UUID(bid), StatementCreate.model_validate(request)
                            )
                        assert Decimal(result["outstanding"]) == 100
                        counts.append(
                            sum(
                                "FROM app.journal_lines" in q or "FROM app.bank_matches" in q
                                for q in queries
                            )
                        )
                        db.rollback()
                else:
                    with select_count(api._transport.app.state.engine) as queries:
                        reply = await api.post(base + "/statements/sign-off", json=request)
                    assert reply.status_code == 201, reply.text
                    counts.append(
                        sum(
                            "FROM app.journal_lines" in q or "FROM app.bank_matches" in q
                            for q in queries
                        )
                    )
            assert counts == [3, 3]  # two item lookups plus one ledger aggregate
            METRICS["reconciliation_item_queries_1_6"] = counts
            request["idempotency_key"] = uuid4().hex
            request["outstanding"][0]["journal_id"] = str(uuid4())
            reply = await api.post(base + "/statements/sign-off", json=request)
            assert (
                reply.status_code == 400 and reply.json()["error"]["code"] == "ERR_RECONCILIATION"
            ), reply.text
            imported = {
                "account_id": accounts["bank"],
                "content": "date,description,amount\n2026-10-31,Reviewed receipt,100\n",
                "mapping": {
                    "date_column": "date",
                    "description_column": "description",
                    "amount_column": "amount",
                },
            }
            preview = await api.post(base + "/bank-imports/preview", json=imported)
            reply = await api.post(
                base + "/bank-imports",
                json={**imported, "expected_hash": preview.json()["file_hash"]},
            )
            assert reply.status_code == 201, reply.text
            row = (await api.get(base + "/bank-transactions")).json()["items"][0]
            reply = await api.post(
                base + f"/bank-transactions/{row['id']}/disposition",
                json={
                    **await command(api, base),
                    "expected_row_version": 0,
                    "action": "categorize",
                    "reason": "Reviewed source",
                    "splits": [{"account_id": accounts["income"], "amount": "100"}],
                },
            )
            assert reply.status_code == 200, reply.text
            # A category-generated journal already represents bank evidence;
            # it cannot be presented as an uncleared item to force a tie-out.
            request.update(
                {
                    **await command(api, base),
                    "as_of": "2026-11-01",
                    "closing_balance": "600",
                    "outstanding": [{"journal_id": reply.json()["journal_id"], "position": 1}],
                }
            )
            reply = await api.post(base + "/statements/sign-off", json=request)
            assert (
                reply.status_code == 400 and reply.json()["error"]["code"] == "ERR_RECONCILIATION"
            ), reply.text
        finally:
            original.unlink(missing_ok=True)


async def test_reminders_use_batch_insert_and_preserve_deduplication(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        for _ in range(6):
            result = await invoice(api, base, accounts, party)
            assert result.status_code == 201, result.text
        app = api._transport.app
        inserts = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO app.notifications"):
                inserts.append(statement)

        event.listen(app.state.engine, "before_cursor_execute", observe)
        try:
            reply = await api.post(base + "/reminders/review", json={"as_of": "2026-12-31"})
            assert reply.status_code == 200 and reply.json()["created"] == 6, reply.text
            assert len(inserts) == 1
            again = await api.post(base + "/reminders/review", json={"as_of": "2026-12-31"})
            assert again.status_code == 200 and again.json()["created"] == 0, again.text
        finally:
            event.remove(app.state.engine, "before_cursor_execute", observe)
        METRICS["notification_insert_statements_for_6_invoices"] = 1


async def test_backdated_loan_checks_do_not_query_each_future_date(
    client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        reply = await api.post(
            base + "/loans",
            json={
                "party_id": party,
                "name": "Scoped synthetic loan",
                "direction": "payable",
                "control_account_id": accounts["loan"],
            },
        )
        assert reply.status_code == 201, reply.text
        path = base + f"/loans/{reply.json()['id']}/movements"
        common = {"bank_account_id": accounts["bank"], "kind": "repayment", "principal": "100"}
        reply = await api.post(
            path,
            json={
                **common,
                **await command(api, base),
                "kind": "drawdown",
                "principal": "1000",
                "accounting_date": "2026-10-01",
            },
        )
        assert reply.status_code == 201, reply.text
        counts = []
        for day in ["2026-10-10", "2026-10-11"]:
            payload = {**common, **await command(api, base), "accounting_date": day}
            with select_count(api._transport.app.state.engine) as queries:
                reply = await api.post(path, json=payload)
            assert reply.status_code == 201, reply.text
            counts.append(len(queries))
            if day == "2026-10-10":
                for future in range(20, 25):
                    reply = await api.post(
                        path,
                        json={
                            **common,
                            **await command(api, base),
                            "accounting_date": f"2026-10-{future}",
                        },
                    )
                    assert reply.status_code == 201, reply.text
        assert counts[0] == counts[1]
        METRICS["loan_repayment_0_5_future_dates"] = counts
        # Funds exist on the requested date, but later repayments forbid this.
        reply = await api.post(
            path,
            json={
                **common,
                **await command(api, base),
                "accounting_date": "2026-10-12",
                "principal": "350",
            },
        )
        assert reply.status_code == 409 and reply.json()["error"]["code"] == "ERR_LOAN_BALANCE", (
            reply.text
        )
        reply = await api.get(base + "/loans", params={"as_of": "2026-10-31"})
        assert reply.status_code == 200 and Decimal(reply.json()["items"][0]["principal"]) == 300, (
            reply.text
        )
