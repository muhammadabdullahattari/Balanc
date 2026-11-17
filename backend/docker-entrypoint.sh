#!/bin/sh
set -e
PORT="${PORT:-8000}"
exec /app/.venv/bin/uvicorn ledgerpilot.main:app --host 0.0.0.0 --port "$PORT" --timeout-graceful-shutdown 30
