"""Local-only credential preparation and real PostgreSQL role verification.

Operator credentials are deliberately outside the API Settings model. Diagnostics
print named checks, never connection strings, passwords, or raw database errors.
"""

import argparse
import io
import os
import re
import secrets
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from ledgerpilot.config import Settings

ROOT = Path(__file__).resolve().parents[3]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SetupError(Exception):
    """An actionable error that contains no private configuration values."""


def read_values(path: Path) -> dict[str, str]:
    return {
        key: value
        for key, value in dotenv_values(path, interpolate=False).items()
        if value is not None
    }


class FileSettings(Settings):
    """Validate exactly the selected file, without ambient or default-file values."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def runtime_settings(values: dict[str, str]) -> Settings:
    # Use the supplied file deterministically; do not mix ambient operator secrets
    # into a check of a different file (for example the CI fixture).
    fields = Settings.model_fields
    return FileSettings.model_validate(
        {key.lower(): value for key, value in values.items() if key.lower() in fields}
    )


def require_local(values: dict[str, str]) -> None:
    if values.get("APP_ENV", "local") != "local":
        raise SetupError("This command is restricted to APP_ENV=local")
    try:
        port = int(values.get("POSTGRES_PORT", "5432"))
    except ValueError:
        raise SetupError("POSTGRES_PORT must be an integer") from None
    if not 1024 <= port <= 65535:
        raise SetupError("Use an unprivileged local PostgreSQL port from 1024 to 65535")
    for name in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        if values.get(name):
            try:
                url = make_url(values[name])
                local = url.host in LOCAL_HOSTS and (url.port or 5432) == port and not url.query
            except (ValueError, TypeError, ArgumentError):
                local = False
            if not local:
                raise SetupError("Local setup requires loopback PostgreSQL on POSTGRES_PORT")


def credential_url(values: dict[str, str], user_key: str, password_key: str) -> str:
    return URL.create(
        "postgresql+psycopg",
        username=values[user_key],
        password=values[password_key],
        host="localhost",
        port=int(values.get("POSTGRES_PORT", "5432")),
        database=values["POSTGRES_DB"],
    ).render_as_string(hide_password=False)


def validate_values(values: dict[str, str]) -> Settings:
    require_local(values)
    required = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "APP_DB_USER",
        "APP_DB_PASSWORD",
        "MIGRATION_DB_USER",
        "MIGRATION_DB_PASSWORD",
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "SESSION_SIGNING_KEY",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise SetupError("Missing local variables: " + ", ".join(missing))
    for name in ("POSTGRES_DB", "POSTGRES_USER", "APP_DB_USER", "MIGRATION_DB_USER"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", values[name]):
            raise SetupError("Database and role names must be lowercase PostgreSQL identifiers")
    roles = [values[name] for name in ("POSTGRES_USER", "APP_DB_USER", "MIGRATION_DB_USER")]
    if len(set(roles)) != 3 or any(name.startswith("pg_") for name in roles):
        raise SetupError("Use three distinct, non-reserved database roles")
    passwords = [
        values[name] for name in ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "MIGRATION_DB_PASSWORD")
    ]
    if len(set(passwords)) != 3 or any(
        len(value) < 16 or value.startswith("replace-") for value in passwords
    ):
        raise SetupError("Use distinct random local database passwords of at least 16 characters")
    for name, user_key, password_key in (
        ("DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"),
        ("MIGRATION_DATABASE_URL", "MIGRATION_DB_USER", "MIGRATION_DB_PASSWORD"),
    ):
        url = make_url(values[name])
        if (
            url.drivername != "postgresql+psycopg"
            or url.username != values[user_key]
            or url.password != values[password_key]
            or url.database != values["POSTGRES_DB"]
        ):
            raise SetupError("Database URLs must match the configured local roles and database")
    try:
        return runtime_settings(values)
    except ValidationError:
        raise SetupError(
            "Runtime configuration is invalid; check names and safe examples"
        ) from None


def prepare_environment(
    path: Path, template: Path, replace_weak: bool = False, port: int | None = None
) -> list[str]:
    """Fill local credentials once, preserving unrelated settings and real secrets."""
    original = path.read_text(encoding="utf-8") if path.exists() else template.read_text("utf-8")
    values = {
        key: value
        for key, value in dotenv_values(stream=io.StringIO(original), interpolate=False).items()
        if value is not None
    }
    require_local(values)
    updated = dict(values)
    if port is not None:
        updated["POSTGRES_PORT"] = str(port)
    for key, value in (
        ("POSTGRES_DB", "ledgerpilot"),
        ("POSTGRES_USER", "ledgerpilot"),
        ("APP_DB_USER", "ledgerpilot_app"),
        ("MIGRATION_DB_USER", "ledgerpilot_migrator"),
    ):
        if not updated.get(key):
            updated[key] = value
    for key in (
        "POSTGRES_PASSWORD",
        "APP_DB_PASSWORD",
        "MIGRATION_DB_PASSWORD",
        "SESSION_SIGNING_KEY",
    ):
        value = updated.get(key, "")
        minimum = 32 if key == "SESSION_SIGNING_KEY" else 16
        if (
            not value
            or value.startswith(("replace-", "local-only-"))
            or (replace_weak and len(value.encode()) < minimum)
        ):
            updated[key] = secrets.token_urlsafe(48)
    for key, user_key, password_key in (
        ("DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"),
        ("MIGRATION_DATABASE_URL", "MIGRATION_DB_USER", "MIGRATION_DB_PASSWORD"),
    ):
        # Align local URLs to the selected roles, retaining the other settings.
        updated[key] = credential_url(updated, user_key, password_key)
    validate_values(updated)
    changed = [key for key in updated if values.get(key) != updated[key]]
    if not changed and path.exists():
        return []
    lines = original.splitlines()
    for key in changed:
        escaped = updated[key].replace("\\", "\\\\").replace("'", "\\'")
        replacement = f"{key}='{escaped}'"
        matches = [
            i for i, line in enumerate(lines) if re.match(rf"^\s*(?:export\s+)?{key}\s*=", line)
        ]
        if len(matches) > 1:
            raise SetupError("Remove duplicate environment variable definitions before preparing")
        if matches:
            lines[matches[0]] = replacement
        else:
            lines.append(replacement)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".ledgerpilot-env-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write("\n".join(lines) + "\n")
        if path.exists():
            os.chmod(temp_name, path.stat().st_mode & 0o777)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return changed


def connect(url: URL) -> psycopg.Connection[tuple[object, ...]]:
    """Connect with bounded timeouts and no sensitive error printing."""
    return psycopg.connect(
        host="127.0.0.1" if url.host == "localhost" else url.host,
        port=url.port or 5432,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=5,
        autocommit=True,
        options="-c statement_timeout=15000 -c lock_timeout=3000",
    )


def denied(
    connection: psycopg.Connection[tuple[object, ...]], statement: sql.SQL | sql.Composed
) -> None:
    try:
        with connection.transaction():
            connection.execute(statement)
            # Roll back even if unexpectedly permitted.
            raise SetupError("A forbidden database operation was unexpectedly permitted")
    except psycopg.errors.InsufficientPrivilege:
        pass


def verify_database(values: dict[str, str], check_rotation: bool = False) -> list[str]:
    """Probe real grants using disposable objects; never create application schema."""
    validate_values(values)
    app_url = make_url(values["DATABASE_URL"])
    migration_url = make_url(values["MIGRATION_DATABASE_URL"])
    admin_url = make_url(credential_url(values, "POSTGRES_USER", "POSTGRES_PASSWORD"))
    checks: list[str] = []
    with connect(admin_url) as admin, connect(migration_url) as migrator, connect(app_url) as app:
        for connection, expected in (
            (app, values["APP_DB_USER"]),
            (migrator, values["MIGRATION_DB_USER"]),
        ):
            role = connection.execute(
                "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
                "rolbypassrls, rolinherit FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            if role != (expected, False, False, False, False, False, False):
                raise SetupError("A database role has unexpected identity or elevated privileges")
            memberships = connection.execute(
                "SELECT count(*) FROM pg_auth_members WHERE member = "
                "(SELECT oid FROM pg_roles WHERE rolname = current_user)"
            ).fetchone()
            if memberships != (0,):
                raise SetupError("Application/migration roles must not inherit other roles")
            privileges = connection.execute(
                "SELECT has_database_privilege(current_database(), 'CREATE'), "
                "has_database_privilege(current_database(), 'TEMP'), "
                "has_schema_privilege('public', 'CREATE')"
            ).fetchone()
            if privileges != (False, False, False):
                raise SetupError("Database or public-schema grants are too broad")
        owner = app.execute(
            "SELECT pg_get_userbyid(nspowner), has_schema_privilege('app', 'USAGE'), "
            "has_schema_privilege('app', 'CREATE') FROM pg_namespace WHERE nspname = 'app'"
        ).fetchone()
        if owner != (values["MIGRATION_DB_USER"], True, False):
            raise SetupError("The app schema owner or runtime grants are incorrect")
        checks.append("Runtime/migration identities, schema ownership and privilege flags")
        table = sql.Identifier("app", "phase1_probe_" + uuid4().hex)
        migrator.execute(
            sql.SQL("CREATE TABLE {} (id BIGSERIAL PRIMARY KEY, value TEXT)").format(table)
        )
        try:
            with app.transaction():
                app.execute(sql.SQL("INSERT INTO {} (value) VALUES ('probe')").format(table))
                app.execute(sql.SQL("UPDATE {} SET value='verified'").format(table))
                if app.execute(sql.SQL("SELECT value FROM {}").format(table)).fetchone() != (
                    "verified",
                ):
                    raise SetupError("Runtime data access failed")
            checks.append("Migrator DDL and runtime SELECT/INSERT/UPDATE with sequence access")
            forbidden = (
                sql.SQL("DELETE FROM {}").format(table),
                sql.SQL("TRUNCATE {}").format(table),
                sql.SQL("ALTER TABLE {} ADD COLUMN forbidden TEXT").format(table),
                sql.SQL("DROP TABLE {}").format(table),
                sql.SQL("CREATE TABLE app.phase1_forbidden (id INT)"),
                sql.SQL("SET ROLE {}").format(sql.Identifier(values["MIGRATION_DB_USER"])),
                sql.SQL("CREATE ROLE phase1_forbidden"),
            )
            for statement in forbidden:
                denied(app, statement)
            checks.append("Runtime DELETE/TRUNCATE/DDL/role escalation denied")
        finally:
            migrator.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))
        if check_rotation:
            replacement = secrets.token_urlsafe(48)
            try:
                admin.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(values["APP_DB_USER"]), sql.Literal(replacement)
                    )
                )
                try:
                    with connect(app_url):
                        pass
                except psycopg.OperationalError as error:
                    # libpq connection failures can lack a structured SQLSTATE.
                    # Do not count a timeout/refused socket as password rejection.
                    if (
                        error.sqlstate != "28P01"
                        and "password authentication failed" not in str(error)
                    ):
                        raise SetupError("Could not verify retired password rejection") from None
                else:
                    raise SetupError("The retired runtime password was not rejected")
                with connect(app_url.set(password=replacement)) as rotated:
                    rotated.execute("SELECT 1")
            finally:
                admin.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(values["APP_DB_USER"]), sql.Literal(app_url.password)
                    )
                )
            with connect(app_url) as restored:
                restored.execute("SELECT 1")
            checks.append(
                "Password rotation: old rejected, replacement accepted, original restored"
            )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "verify"))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--port", type=int, help="Set an alternate loopback port during prepare")
    parser.add_argument(
        "--replace-weak-local-secrets",
        action="store_true",
        help="Replace undersized secrets before first local DB start or during planned rotation",
    )
    parser.add_argument(
        "--check-rotation",
        action="store_true",
        help="Temporarily rotate/restore local app password; stop API/workers first",
    )
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            changed = prepare_environment(
                args.env_file, ROOT / ".env.example", args.replace_weak_local_secrets, args.port
            )
            print(
                "Local credential file prepared; values are hidden. Updated names: "
                + (", ".join(changed) if changed else "none")
            )
        else:
            values = read_values(args.env_file)
            validate_values(values)
            print("PASS: local configuration, credential separation and runtime validation")
            if args.command == "verify":
                for check in verify_database(values, args.check_rotation):
                    print("PASS: " + check)
    except SetupError as error:
        print("FAIL: " + str(error))
        return 1
    except (psycopg.Error, SQLAlchemyError, OSError, ValueError):
        print(
            "FAIL: local setup could not complete; check service health and private configuration"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
