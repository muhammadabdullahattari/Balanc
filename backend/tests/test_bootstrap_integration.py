"""Opt-in checks against the actual local Compose PostgreSQL bootstrap."""

import os
import subprocess

import pytest

from ledgerpilot.local_setup import ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_BOOTSTRAP_TESTS") != "1",
    reason="Requires running local Compose PostgreSQL; enabled in database CI",
)


@pytest.mark.parametrize(
    "override",
    [
        'export APP_DB_USER="$POSTGRES_USER"',
        'export APP_DB_USER="$MIGRATION_DB_USER"',
        'export APP_DB_PASSWORD="$MIGRATION_DB_PASSWORD"',
        "export APP_DB_PASSWORD=short",
        "export MIGRATION_DB_USER=unexpected_schema_owner",
    ],
)
def test_bootstrap_rejects_unsafe_changes(override: str) -> None:
    result = subprocess.run(
        [
            "docker", "compose", "exec", "--no-TTY", "postgres", "sh", "-c",
            override + '; psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" '
            '-d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/01-roles.sql',
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "Unsafe bootstrap configuration" in result.stdout
