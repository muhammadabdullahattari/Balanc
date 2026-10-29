"""Migrations use only the operator's migration identity, never the runtime URL."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import make_url

from ledgerpilot import models  # noqa: F401
from ledgerpilot.database import Base
from ledgerpilot.local_setup import ROOT, read_values

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
values = read_values(ROOT / ".env")
url = os.environ.get("MIGRATION_DATABASE_URL") or values.get("MIGRATION_DATABASE_URL")
runtime = os.environ.get("APP_DB_USER") or values.get("APP_DB_USER", "ledgerpilot_app")
migrator = os.environ.get("MIGRATION_DB_USER") or values.get(
    "MIGRATION_DB_USER", "ledgerpilot_migrator"
)
if not url or make_url(url).username != migrator or migrator == runtime:
    raise RuntimeError("Configure a separate MIGRATION_DATABASE_URL and migration identity")
if make_url(url).drivername != "postgresql+psycopg":
    raise RuntimeError("Migrations require PostgreSQL psycopg")
parsed = make_url(url)
if (
    not parsed.host
    or not parsed.database
    or not parsed.password
    or set(parsed.query) - {"sslmode", "sslrootcert"}
    or any(not isinstance(value, str) for value in parsed.query.values())
):
    raise RuntimeError("Use a complete migration URL without identity or host query overrides")
profile = os.environ.get("APP_ENV") or values.get("APP_ENV", "local")
if profile not in {"local", "test"} and parsed.query.get("sslmode") != "verify-full":
    raise RuntimeError("Hosted migrations require verified TLS")
config.attributes["runtime_role"] = runtime
if context.is_offline_mode():
    raise RuntimeError("Use connected migrations to verify role and schema ownership")
engine = create_engine(
    url,
    poolclass=pool.NullPool,
    hide_parameters=True,
    connect_args={
        "connect_timeout": 5,
        "options": "-c lock_timeout=3000 -c statement_timeout=30000",
    },
)
with engine.connect() as connection:
    safe = connection.execute(
        text("""
        SELECT current_user=:role AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
          OR r.rolreplication OR r.rolbypassrls)
        FROM pg_roles r JOIN pg_namespace n ON n.nspowner=r.oid
        WHERE r.rolname=current_user AND n.nspname='app'
    """),
        {"role": migrator},
    ).scalar_one_or_none()
    if not safe:
        raise RuntimeError("Migrations require the restricted owner of the app schema")
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table_schema="app",
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
