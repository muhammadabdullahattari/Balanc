"""Celery application configuration for LedgerPilot background jobs."""

import os

from celery import Celery  # type: ignore[import-untyped]

broker_url = (
    os.environ.get("CELERY_BROKER_URL")
    or os.environ.get("RATE_REDIS_URL")
    or os.environ.get("REDIS_URL")
    or "redis://localhost:6379/0"
)
result_backend = os.environ.get("CELERY_RESULT_BACKEND") or broker_url

celery_app = Celery(
    "ledgerpilot",
    broker=broker_url,
    backend=result_backend,
    include=["ledgerpilot.identity.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
)
