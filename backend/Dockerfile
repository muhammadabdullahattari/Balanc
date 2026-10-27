# Build from the backend directory so the lockfile and project metadata are in context.
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-install-project \
    && uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)" || exit 1
CMD ["/app/.venv/bin/uvicorn", "ledgerpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]

