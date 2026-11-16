"""Automatic schema initialization and test role seeding for managed platforms."""

import logging
import os
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from ledgerpilot.config import Settings
from ledgerpilot.identity.security import hash_password

logger = logging.getLogger("ledgerpilot.bootstrap")


def run_migrations_and_seed_if_needed(engine: Engine, settings: Settings) -> None:
    """Initialize schema and seed initial test roles on Railway/managed Postgres."""
    # 1. Run migrations if version is not 0030
    needs_migration = False
    with engine.connect() as connection:
        db_user: str = connection.execute(text("SELECT current_user")).scalar_one()
        is_hosted = (
            db_user == "postgres"
            or settings.app_db_user == "postgres"
            or bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_NAME"))
        )
        if not is_hosted:
            return
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        connection.commit()
        try:
            version = connection.execute(
                text("SELECT version_num FROM app.alembic_version")
            ).scalar_one_or_none()
            if version != "0030":
                needs_migration = True
        except SQLAlchemyError:
            needs_migration = True

    if needs_migration:
        logger.info("[BOOTSTRAP] Running Alembic migrations to head...")
        backend_dir = Path(__file__).resolve().parents[2]
        ini_candidates = [
            backend_dir / "alembic.ini",
            Path("/app/alembic.ini"),
            Path.cwd() / "alembic.ini",
            Path.cwd() / "backend/alembic.ini",
        ]
        ini_path = next((p for p in ini_candidates if p.exists()), backend_dir / "alembic.ini")

        migrations_candidates = [
            backend_dir / "migrations",
            Path("/app/migrations"),
            Path.cwd() / "migrations",
            Path.cwd() / "backend/migrations",
        ]
        migrations_path = next(
            (p for p in migrations_candidates if p.exists()), backend_dir / "migrations"
        )

        alembic_cfg = Config(str(ini_path))
        alembic_cfg.set_main_option("script_location", str(migrations_path))
        alembic_cfg.attributes["runtime_role"] = "postgres"

        try:
            with engine.connect() as mig_conn:
                alembic_cfg.attributes["connection"] = mig_conn
                command.upgrade(alembic_cfg, "head")
                mig_conn.commit()
            logger.info("[BOOTSTRAP] Alembic migrations successfully applied to 0030.")
            print("[BOOTSTRAP] Alembic migrations successfully applied to 0030.", flush=True)
        except Exception as e:
            logger.exception("[BOOTSTRAP] Database migration failed: %s", e)
            print(
                f"[BOOTSTRAP ERROR] Database migration failed ({type(e).__name__}): {e}",
                flush=True,
            )
            return

    # 2. Seed test roles and workspace
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL row_security = off"))
        existing_owner = conn.execute(
            text("SELECT id FROM app.users WHERE email = 'owner@balanc.local'")
        ).scalar_one_or_none()

        if existing_owner is None:
            logger.info("[BOOTSTRAP] Seeding test workspace and users...")
            print("[BOOTSTRAP] Seeding test workspace and users...", flush=True)
            owner_id = uuid4()
            accountant_id = uuid4()
            business_id = UUID("42132f25-4948-45c0-9142-8def637b0a9c")
            pwd_hash = hash_password("Password123!Secure")

            # Insert Owner
            conn.execute(
                text("""
                INSERT INTO app.users (id, email, password_hash, verified, suspended)
                VALUES (:id, 'owner@balanc.local', :hash, true, false)
                ON CONFLICT (email) DO NOTHING
            """),
                {"id": owner_id, "hash": pwd_hash},
            )

            # Insert Accountant
            conn.execute(
                text("""
                INSERT INTO app.users (id, email, password_hash, verified, suspended)
                VALUES (:id, 'accountant@balanc.local', :hash, true, false)
                ON CONFLICT (email) DO NOTHING
            """),
                {"id": accountant_id, "hash": pwd_hash},
            )

            # Insert Business Workspace
            conn.execute(
                text("""
                INSERT INTO app.businesses (
                    id, name, currency, currency_precision, timezone,
                    basis, fiscal_month, jurisdiction, policy_version, version
                )
                VALUES (
                    :bid, 'Acme Test Corp', 'USD', 2, 'America/New_York',
                    'accrual', 1, 'US', 1, 0
                )
                ON CONFLICT (id) DO NOTHING
            """),
                {"bid": business_id},
            )

            # Insert Memberships
            conn.execute(
                text("""
                INSERT INTO app.memberships (
                    business_id, user_id, role, can_post, can_invite, active, version
                )
                VALUES (:bid, :uid, 'owner', true, true, true, 0)
                ON CONFLICT (business_id, user_id) DO NOTHING
            """),
                {"bid": business_id, "uid": owner_id},
            )

            conn.execute(
                text("""
                INSERT INTO app.memberships (
                    business_id, user_id, role, can_post, can_invite, active, version
                )
                VALUES (:bid, :uid, 'accountant', true, true, true, 0)
                ON CONFLICT (business_id, user_id) DO NOTHING
            """),
                {"bid": business_id, "uid": accountant_id},
            )

            # Insert Policy History
            conn.execute(
                text("""
                INSERT INTO app.policy_history (
                    id, business_id, version, effective_date, basis, jurisdiction
                )
                VALUES (gen_random_uuid(), :bid, 1, current_date, 'accrual', 'US')
            """),
                {"bid": business_id},
            )

            # Insert standard Chart of Accounts
            accounts = [
                ("1000", "Operating Bank Account", "asset", "bank"),
                ("1200", "Accounts Receivable", "asset", "receivable"),
                ("2000", "Accounts Payable", "liability", "payable"),
                ("3000", "Owner's Equity", "equity", None),
                ("4000", "Sales Revenue", "income", None),
                ("5000", "Operating Expenses", "expense", None),
            ]
            for code, name, kind, control in accounts:
                conn.execute(
                    text("""
                    INSERT INTO app.accounts (
                        id, business_id, code, name, kind, control, active, version
                    )
                    VALUES (gen_random_uuid(), :bid, :code, :name, :kind, :ctrl, true, 0)
                    ON CONFLICT (business_id, code) DO NOTHING
                """),
                    {
                        "bid": business_id,
                        "code": code,
                        "name": name,
                        "kind": kind,
                        "ctrl": control,
                    },
                )

            logger.info(
                "[BOOTSTRAP] Successfully created test workspace 'Acme Test Corp' "
                "with Owner (owner@balanc.local) and Accountant (accountant@balanc.local)"
            )
            print(
                "[BOOTSTRAP] Successfully created test workspace 'Acme Test Corp' "
                "with Owner (owner@balanc.local) and Accountant (accountant@balanc.local)",
                flush=True,
            )
