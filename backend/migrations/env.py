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

existing_conn = config.attributes.get("connection")
if existing_conn is not None and not getattr(existing_conn, "closed", False):
    runtime_role = config.attributes.get("runtime_role", "postgres")
    role_exists = existing_conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": runtime_role},
    ).scalar_one_or_none()
    if not role_exists:
        runtime_role = "postgres"
    config.attributes["runtime_role"] = runtime_role

    existing_conn.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
    existing_conn.execute(text("SET search_path TO public, pg_catalog"))
    existing_conn.commit()
    existing_conn.dialect.default_schema_name = "public"

    def include_name(name, type_, parent_names):
        if type_ == "schema":
            return name == "app"
        return True

    def include_object(object, name, type_, reflected, compare_to):
        if type_ == "table":
            if getattr(object, "schema", None) != "app":
                return False
            if reflected and compare_to is None:
                return False
            return True
        elif type_ in ("index", "foreign_key_constraint", "unique_constraint"):
            return False
        return True

    context.configure(
        connection=existing_conn,
        target_metadata=Base.metadata,
        version_table_schema="app",
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        compare_type=False,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    values = read_values(ROOT / ".env")
    url = os.environ.get("MIGRATION_DATABASE_URL") or values.get("MIGRATION_DATABASE_URL")
    runtime = os.environ.get("APP_DB_USER") or values.get("APP_DB_USER", "ledgerpilot_app")
    migrator = os.environ.get("MIGRATION_DB_USER") or values.get(
        "MIGRATION_DB_USER", "ledgerpilot_migrator"
    )
    if (
        runtime == "postgres"
        or (url and make_url(url).username == "postgres")
        or (os.environ.get("DATABASE_URL") and make_url(os.environ["DATABASE_URL"]).username == "postgres")
    ):
        runtime = "postgres"
        migrator = "postgres"
        url = url or os.environ.get("DATABASE_URL")
        if url and url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url and url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if not url:
        raise RuntimeError("Configure a separate MIGRATION_DATABASE_URL and migration identity")
    if runtime != "postgres" and (make_url(url).username != migrator or migrator == runtime):
        raise RuntimeError("Configure a separate MIGRATION_DATABASE_URL and migration identity")
    if make_url(url).drivername != "postgresql+psycopg":
        raise RuntimeError("Migrations require PostgreSQL psycopg")
    parsed = make_url(url)
    if runtime != "postgres":
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
        if migrator == "postgres":
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            connection.commit()
            role_exists = connection.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": runtime},
            ).scalar_one_or_none()
            if not role_exists:
                runtime = "postgres"
                config.attributes["runtime_role"] = "postgres"
        else:
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
        connection.execute(text("SET search_path TO public, pg_catalog"))
        connection.commit()
        connection.dialect.default_schema_name = "public"

        def include_name(name, type_, parent_names):
            if type_ == "schema":
                return name == "app"
            return True

        def include_object(object, name, type_, reflected, compare_to):
            if type_ == "table":
                if getattr(object, "schema", None) != "app":
                    return False
                if reflected and compare_to is None:
                    return False
                return True
            elif type_ in ("index", "foreign_key_constraint", "unique_constraint"):
                return False
            return True

        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table_schema="app",
            include_schemas=True,
            include_name=include_name,
            include_object=include_object,
            compare_type=False,
            compare_server_default=False,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
