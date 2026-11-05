"""Regression coverage for deployment-audit fixes; real database and TCP peers."""

import asyncio
import os
import socket
import threading
import time

import httpx
import pytest
from sqlalchemy import select, text
from test_phase2_integration import accountant_context, client, database_settings  # noqa: F401
from test_phase3_integration import invoice, setup

from ledgerpilot.config import Settings
from ledgerpilot.documents import storage
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace import exports, service

# ruff: noqa: F811
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL/Mailpit"
)


@pytest.mark.parametrize("mode", ["drip", "stalled_write"])
def test_scanner_total_deadline_with_real_tcp_peer(monkeypatch, mode):
    monkeypatch.setattr(storage, "SCAN_DEADLINE_SECONDS", 0.3)
    stop = threading.Event()
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()

        def peer():
            with server.accept()[0] as conn:
                try:
                    if mode == "drip":
                        for value in b"1: ClamAV synthetic\0":
                            conn.sendall(bytes([value]))
                            if stop.wait(0.08):
                                return
                    else:
                        conn.recv(1024)
                        conn.sendall(b"1: ClamAV synthetic\0")
                        stop.wait(2)
                except OSError:
                    pass

        worker = threading.Thread(target=peer)
        worker.start()
        start = time.monotonic()
        try:
            with pytest.raises(DomainError) as error:
                storage.scan(
                    b"x" * (16 * 1024 * 1024),
                    Settings(
                        _env_file=None,
                        database_url="postgresql+psycopg://ledgerpilot_app:synthetic@127.0.0.1:1/test",
                        session_signing_key="synthetic-signing-key-12345678901234567890",
                        clamav_host="127.0.0.1",
                        clamav_port=server.getsockname()[1],
                    ),
                )
            assert error.value.code == "ERR_SCAN_UNAVAILABLE"
            assert time.monotonic() - start < 1.5
        finally:
            stop.set()
            worker.join(3)


@REAL
async def test_export_snapshot_does_not_block_posting_and_excludes_later_commit(
    client: httpx.AsyncClient, database_settings, monkeypatch
):
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        entered, release = threading.Event(), threading.Event()
        original = service.journal_csv
        captured = {}

        def paused(db, business_id, start, end):
            captured["isolation"] = db.scalar(text("SHOW transaction_isolation"))
            entered.set()
            assert release.wait(8)
            return original(db, business_id, start, end)

        monkeypatch.setattr(service, "journal_csv", paused)
        task = asyncio.create_task(
            api.post(
                base + "/exports/snapshots",
                json={
                    "idempotency_key": "snapshot-concurrency-test",
                    "format": "journals_csv",
                    "start": "2026-10-01",
                    "end": "2026-10-31",
                },
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            issued = await asyncio.wait_for(invoice(api, base, accounts, party), 4)
            assert issued.status_code == 201, issued.text
        finally:
            release.set()
        exported = await task
        assert captured["isolation"] == "serializable"
        # PostgreSQL may reject persistence when a referenced business row changed.
        # A safe conflict is preferable to mixing snapshots; caller explicitly retries.
        conflicted = exported.status_code == 409
        if conflicted:
            assert exported.json()["error"]["code"] == "ERR_CONFLICT"
            exported = await api.post(
                base + "/exports/snapshots",
                json={
                    "idempotency_key": "snapshot-concurrency-test",
                    "format": "journals_csv",
                    "start": "2026-10-01",
                    "end": "2026-10-31",
                },
            )
        assert exported.status_code == 201, exported.text
        body = await api.get(base + "/exports/snapshots/" + exported.json()["id"] + "/download")
        assert body.status_code == 200
        assert len(body.text.splitlines()) == (3 if conflicted else 1)
        # Either coherent pre-posting data or a new coherent snapshot, never partial rows.


@REAL
async def test_export_metadata_cardinality_and_bytes_reject_before_loading(
    client, database_settings, record_property
):
    import tracemalloc

    from sqlalchemy import literal_column

    async with accountant_context(client, database_settings) as (_, api):
        with api._transport.app.state.sessions.begin() as db:
            with pytest.raises(DomainError) as error:
                exports.bounded_scalars(db, select(literal_column("generate_series(1, 10001)")))
            assert error.value.code == "ERR_QUOTA"
            tracemalloc.start()
            try:
                with pytest.raises(DomainError) as error:
                    exports.bounded_scalars(db, select(literal_column("repeat('x', 11000000)")))
                assert error.value.code == "ERR_QUOTA"
                _, peak = tracemalloc.get_traced_memory()
                record_property("oversized_metadata_python_peak_bytes", peak)
                assert peak < 2 * 1024 * 1024  # Payload rejected in PostgreSQL, not hydrated.
            finally:
                tracemalloc.stop()


@REAL
async def test_report_snapshot_keeps_one_version_across_concurrent_posting(
    client, database_settings
):
    from uuid import UUID

    from sqlalchemy import func

    from ledgerpilot.accounting.models import Journal
    from ledgerpilot.database import set_context
    from ledgerpilot.tenancy.models import Business

    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        uid = (await api.get("/api/v1/auth/me")).json()["id"]
        with api._transport.app.state.sessions.begin() as report:
            report.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            set_context(report, UUID(uid), UUID(bid))
            version = report.scalar(select(Business.version).where(Business.id == UUID(bid)))
            count = report.scalar(select(func.count()).select_from(Journal))
            issued = await asyncio.wait_for(invoice(api, base, accounts, party), 4)
            assert issued.status_code == 201, issued.text
            assert (
                report.scalar(select(Business.version).where(Business.id == UUID(bid))) == version
            )
            assert report.scalar(select(func.count()).select_from(Journal)) == count
        with api._transport.app.state.sessions.begin() as fresh:
            set_context(fresh, UUID(uid), UUID(bid))
            assert fresh.scalar(select(func.count()).select_from(Journal)) == count + 1


@REAL
async def test_snapshot_read_works_with_a_single_connection_pool(client, database_settings):
    from sqlalchemy.orm import sessionmaker

    from ledgerpilot.database import build_engine

    async with accountant_context(client, database_settings) as (bid, api):
        app = api._transport.app
        original = app.state.sessions
        engine = build_engine(
            database_settings.model_copy(
                update={
                    "db_pool_size": 1,
                    "db_max_overflow": 0,
                    "db_pool_timeout_seconds": 1,
                }
            )
        )
        try:
            app.state.sessions = sessionmaker(engine, expire_on_commit=False)
            result = await api.get(f"/api/v1/businesses/{bid}")
            assert result.status_code == 200, result.text
        finally:
            app.state.sessions = original
            engine.dispose()
