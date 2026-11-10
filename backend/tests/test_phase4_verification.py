"""Independent financial assertions and a real, synthetic PostgreSQL restore drill."""

import hashlib
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command as migrations
from alembic.config import Config
from cryptography.fernet import Fernet
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)
from test_phase3_integration import command, invoice, setup

from ledgerpilot.documents import storage
from ledgerpilot.local_setup import ROOT, FileSettings, connect, credential_url, read_values
from ledgerpilot.main import create_app

# ruff: noqa: F811 -- shared real-service fixtures
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL and SMTP"
)


def snapshot(url: URL) -> dict[str, tuple[int, str]]:
    """Hash every application row without exposing authentication or financial values."""
    result = {}
    with connect(url) as db:
        tables = db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='app' ORDER BY tablename"
        ).fetchall()
        for (table,) in tables:
            rows = db.execute(
                sql.SQL(
                    "SELECT row_to_json(t)::text FROM app.{} t ORDER BY row_to_json(t)::text"
                ).format(sql.Identifier(table))
            ).fetchall()
            result[table] = (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest())
    return result


async def test_loan_principal_never_becomes_profit_and_interest_stays_separate(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        loan = await api.post(
            base + "/loans",
            json={
                "party_id": party,
                "direction": "payable",
                "control_account_id": accounts["loan"],
                "name": "Synthetic Phase 4 loan",
            },
        )
        assert loan.status_code == 201, loan.text
        for kind in ("drawdown", "repayment"):
            response = await api.post(
                base + f"/loans/{loan.json()['id']}/movements",
                json={
                    **await command(api, base),
                    "kind": kind,
                    "accounting_date": "2026-10-02",
                    "bank_account_id": accounts["bank"],
                    "principal": "100",
                    "interest": "10" if kind == "repayment" else "0",
                    "interest_account_id": accounts["interest"],
                },
            )
            assert response.status_code == 201, response.text
        trial = await api.get(base + "/trial-balance", params={"as_of": "2026-10-31"})
        assert trial.status_code == 200, trial.text
        amounts = {
            r["account_id"]: Decimal(r["debit"]) - Decimal(r["credit"])
            for r in trial.json()["items"]
        }
        assert amounts[accounts["loan"]] == 0
        assert amounts[accounts["bank"]] == -10
        assert amounts[accounts["interest"]] == 10
        pnl = await api.get(
            base + "/reports/profit-loss", params={"start": "2026-10-01", "end": "2026-10-31"}
        )
        assert pnl.status_code == 200, pnl.text
        assert Decimal(pnl.json()["income"]) == 0
        assert Decimal(pnl.json()["expense"]) == 10


def test_previous_supported_schema_upgrades_without_losing_identity(
    database_settings: FileSettings,
) -> None:
    values = read_values(ROOT / ".env")
    admin_url = make_url(credential_url(values, "POSTGRES_USER", "POSTGRES_PASSWORD"))
    name = "ledgerpilot_upgrade_" + uuid4().hex
    user_id = uuid4()
    migrator = values["MIGRATION_DB_USER"]
    with connect(admin_url) as db:
        db.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(migrator)
            )
        )
    try:
        with connect(admin_url.set(database=name)) as db:
            db.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            db.execute(
                sql.SQL("CREATE SCHEMA app AUTHORIZATION {}").format(sql.Identifier(migrator))
            )
            db.execute(
                sql.SQL("GRANT USAGE ON SCHEMA app TO {}").format(
                    sql.Identifier(values["APP_DB_USER"])
                )
            )
            db.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                ).format(sql.Identifier(migrator))
            )
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend/migrations"))
        url = make_url(values["MIGRATION_DATABASE_URL"]).set(database=name, host="127.0.0.1")
        with pytest.MonkeyPatch.context() as env:
            env.setenv("MIGRATION_DATABASE_URL", url.render_as_string(hide_password=False))
            env.setenv("APP_DB_USER", values["APP_DB_USER"])
            env.setenv("MIGRATION_DB_USER", migrator)
            migrations.upgrade(config, "0019")
            with connect(admin_url.set(database=name)) as db:
                db.execute(
                    "INSERT INTO app.users(id,email,password_hash) VALUES(%s,%s,%s)",
                    (user_id, f"{user_id}@example.com", "synthetic-old-hash"),
                )
            migrations.upgrade(config, "head")
            migrations.upgrade(config, "head")
        with connect(admin_url.set(database=name)) as db:
            assert db.execute(
                "SELECT password_hash,password_version FROM app.users WHERE id=%s", (user_id,)
            ).fetchone() == ("synthetic-old-hash", 0)
            db.execute(
                "UPDATE app.users SET password_hash=%s WHERE id=%s", ("synthetic-new-hash", user_id)
            )
            assert db.execute(
                "SELECT password_version FROM app.users WHERE id=%s", (user_id,)
            ).fetchone() == (1,)
            assert db.execute("SELECT version_num FROM app.alembic_version").fetchone() == ("0022",)
    finally:
        assert name.startswith("ledgerpilot_upgrade_")
        with connect(admin_url) as db:
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


async def test_synthetic_backup_restores_rows_grants_rls_and_financial_reads(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
    tmp_path: Path,
) -> None:
    async with accountant_context(client, database_settings) as (bid, api):
        base, accounts, party = await setup(api, bid)
        issued = await invoice(api, base, accounts, party)
        assert issued.status_code == 201, issued.text
        payment = await api.post(
            base + "/payments",
            json={
                **await command(api, base),
                "kind": "receipt",
                "party_id": party,
                "accounting_date": "2026-11-01",
                "bank_account_id": accounts["bank"],
                "control_account_id": accounts["ar"],
                "amount": "40",
                "allocations": [{"document_id": issued.json()["id"], "amount": "40"}],
            },
        )
        assert payment.status_code == 201, payment.text
        original = b"date,amount\n2026-11-01,40\n"
        source_settings = database_settings.model_copy(
            update={
                "evidence_storage_root": tmp_path / "source-evidence",
                "evidence_encryption_key": SecretStr(Fernet.generate_key().decode()),
            }
        )
        source_app = create_app(source_settings)
        async with (
            source_app.router.lifespan_context(source_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=source_app),
                base_url="https://api.ledgerpilot.test",
                cookies=api.cookies,
                headers=dict(api.headers),
            ) as source_client,
        ):
            upload = await source_client.post(
                base + "/evidence/upload",
                params={"filename": "synthetic-statement.csv"},
                content=original,
                headers={"Content-Type": "application/octet-stream"},
            )
        assert upload.status_code == 201, upload.text
        evidence_id = upload.json()["id"]
        job_id = upload.json()["job_id"]
        # The synthetic source has no concurrent writers during the database and file copy.
        evidence_backup = tmp_path / "backup-evidence"
        shutil.copytree(source_settings.evidence_storage_root, evidence_backup)
        values = read_values(ROOT / ".env")
        source = make_url(database_settings.database_url.get_secret_value()).database
        assert source and source.startswith("ledgerpilot_test_")
        restored = "ledgerpilot_restore_" + uuid4().hex
        admin_url = make_url(credential_url(values, "POSTGRES_USER", "POSTGRES_PASSWORD"))
        # Discover only this repository's existing Compose service, never a guessed container.
        container = (
            subprocess.run(
                ["docker", "compose", "ps", "-q", "postgres"],
                cwd=ROOT,
                capture_output=True,
                check=True,
                timeout=30,
            )
            .stdout.decode()
            .strip()
        )
        assert container
        user = values["POSTGRES_USER"]
        dump = subprocess.run(
            ["docker", "exec", container, "pg_dump", "-U", user, "-Fc", source],
            capture_output=True,
            timeout=90,
        )
        assert dump.returncode == 0, "Synthetic pg_dump failed (diagnostics withheld)"
        expected = snapshot(admin_url.set(database=source))
        with connect(admin_url) as db:
            db.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(restored), sql.Identifier(values["MIGRATION_DB_USER"])
                )
            )
            db.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(restored))
            )
            db.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {},{}").format(
                    sql.Identifier(restored),
                    sql.Identifier(values["APP_DB_USER"]),
                    sql.Identifier(values["MIGRATION_DB_USER"]),
                )
            )
        try:
            restore = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "pg_restore",
                    "-U",
                    user,
                    "--exit-on-error",
                    "-d",
                    restored,
                ],
                input=dump.stdout,
                capture_output=True,
                timeout=90,
            )
            assert restore.returncode == 0, "Synthetic pg_restore failed (diagnostics withheld)"
            assert snapshot(admin_url.set(database=restored)) == expected
            runtime = make_url(database_settings.database_url.get_secret_value()).set(
                database=restored
            )
            with connect(runtime) as db:
                assert db.execute("SELECT count(*) FROM app.businesses").fetchone()[0] == 0
                assert db.execute(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ).fetchone() == (False,)
                assert db.execute("SELECT version_num FROM app.alembic_version").fetchone() == (
                    "0022",
                )
            restored_root = tmp_path / "restored-evidence"
            shutil.copytree(evidence_backup, restored_root)
            settings = source_settings.model_copy(
                update={
                    "database_url": SecretStr(runtime.render_as_string(hide_password=False)),
                    "evidence_storage_root": restored_root,
                }
            )
            with connect(admin_url.set(database=restored)) as db:
                evidence_row = db.execute(
                    "SELECT object_key,content_hash FROM app.evidence "
                    "WHERE business_id=%s AND id=%s",
                    (bid, evidence_id),
                ).fetchone()
                assert evidence_row is not None
                object_key, content_hash = evidence_row
                assert content_hash == hashlib.sha256(original).hexdigest()
                assert storage.get(settings, object_key, content_hash) == original
                assert db.execute(
                    "SELECT state,evidence_id::text FROM app.evidence_jobs "
                    "WHERE business_id=%s AND id=%s",
                    (bid, job_id),
                ).fetchone() == ("queued", evidence_id)
            app = create_app(settings)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="https://api.ledgerpilot.test",
                    cookies=api.cookies,
                    headers=dict(api.headers),
                ) as recovered,
            ):
                ready = await recovered.get("/health/ready")
                assert ready.status_code == 200, ready.text
                detail = await recovered.get(
                    base + "/trade-documents/" + issued.json()["id"], params={"as_of": "2026-11-30"}
                )
                assert detail.status_code == 200, detail.text
                assert Decimal(detail.json()["outstanding"]) == 60
                trial = await recovered.get(base + "/trial-balance", params={"as_of": "2026-11-30"})
                assert trial.status_code == 200, trial.text
                balances = {
                    r["account_id"]: Decimal(r["debit"]) - Decimal(r["credit"])
                    for r in trial.json()["items"]
                }
                assert balances[accounts["bank"]] == 40
                assert balances[accounts["ar"]] == 60
                assert balances[accounts["income"]] == -100
                assert sum(balances.values()) == 0
                evidence = await recovered.get(base + "/evidence")
                assert evidence.status_code == 200, evidence.text
                assert any(
                    item["id"] == evidence_id and item["content_hash"] == content_hash
                    for item in evidence.json()["items"]
                )
                blocked = await recovered.get(base + f"/evidence/{evidence_id}/download")
                assert blocked.status_code == 409
                denied = await recovered.get(f"/api/v1/businesses/{uuid4()}")
                assert denied.status_code == 404
        finally:
            assert restored.startswith("ledgerpilot_restore_") and restored != source
            with connect(admin_url) as db:
                db.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(restored))
                )


async def test_openapi_security_and_docs_match_the_browser_contract(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert spec["openapi"] == "3.1.0"
    assert spec["components"]["securitySchemes"]["BrowserSession"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-lp_access",
    }
    for path, methods in spec["paths"].items():
        for verb, operation in methods.items():
            if verb not in {"get", "post", "patch"}:
                continue
            if path.startswith("/api/v1/businesses"):
                assert operation.get("security") == [{"BrowserSession": []}], path
    docs = await client.get("/docs")
    assert docs.status_code == 200 and "SwaggerUIBundle" in docs.text
    assert "requestInterceptor:" in docs.text and "X-CSRF-Token" in docs.text
    public = create_app(database_settings.model_copy(update={"app_env": "demo"}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public), base_url="https://api.test"
    ) as api:
        assert (await api.get("/docs")).status_code == 404
        assert (await api.get("/openapi.json")).status_code == 404
