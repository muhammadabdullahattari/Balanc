"""Explicit non-secret configuration for service-independent unit tests."""

import os

# Define before test modules import the FastAPI app. DB integration checks use
# local_setup against their own isolated PostgreSQL service instead.
os.environ["APP_ENV"] = "test"
os.environ["APP_DB_USER"] = "ledgerpilot_app"
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://ledgerpilot_app:unit-test-only-password@localhost:5432/ledgerpilot"
)
os.environ["SESSION_SIGNING_KEY"] = "unit-test-only-signing-key-never-use-outside-tests"
os.environ["FRONTEND_ORIGINS"] = "http://localhost:5173"
