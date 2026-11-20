"""Celery tasks for identity email dispatch."""

import logging
from typing import Any

from ledgerpilot.celery_app import celery_app

logger = logging.getLogger("ledgerpilot.identity.tasks")


@celery_app.task(  # type: ignore[untyped-decorator]
    name="ledgerpilot.identity.send_verification_email",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_verification_email_task(
    self: Any,
    recipient: str,
    purpose: str,
    token: str,
) -> bool:
    from ledgerpilot.config import get_settings
    from ledgerpilot.identity import mail

    settings = get_settings()
    try:
        mail.deliver(settings, recipient, purpose, token)
        logger.info("Verification email task successfully delivered to %s", recipient)
        return True
    except Exception as exc:
        logger.warning(
            "Verification email task delivery failed for %s: %s; retrying...",
            recipient,
            exc,
        )
        raise self.retry(exc=exc) from exc
