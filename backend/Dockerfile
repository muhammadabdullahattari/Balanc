# Build from the backend directory so the lockfile and project metadata are in context.
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# hadolint ignore=DL3008
RUN apt-get update && apt-get install --no-install-recommends -y zlib1g ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev \
    && useradd --create-home --uid 10001 appuser
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health/ready', timeout=3)"]
CMD ["/bin/sh", "-c", "exec /app/.venv/bin/uvicorn ledgerpilot.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 30"]

