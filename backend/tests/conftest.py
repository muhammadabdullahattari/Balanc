"""Explicit non-secret configuration for service-independent unit tests."""

import os
from collections.abc import Generator

import pytest

from ledgerpilot.testing.smtp_server import TestSMTPServer, ensure_test_smtp_server

# Define before test modules import the FastAPI app. DB integration checks use
# local_setup against their own isolated PostgreSQL service instead.
os.environ["APP_ENV"] = "test"
os.environ["APP_DB_USER"] = "ledgerpilot_app"
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://ledgerpilot_app:unit-test-only-password@localhost:5432/ledgerpilot"
)
os.environ["SESSION_SIGNING_KEY"] = "unit-test-only-signing-key-never-use-outside-tests"
os.environ["FRONTEND_ORIGINS"] = "http://localhost:5173"


@pytest.fixture(scope="session", autouse=True)
def _auto_smtp_test_server() -> Generator[TestSMTPServer, None, None]:
    server = ensure_test_smtp_server()
    try:
        yield server
    finally:
        server.stop()
