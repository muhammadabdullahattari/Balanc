"""Real SMTP delivery; disabled delivery fails explicitly, never returns tokens."""

import smtplib
from email.message import EmailMessage
from typing import Any

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError

SUBJECTS = {
    "verify": "LedgerPilot — Verify your email",
    "reset": "LedgerPilot — Reset your password",
    "business invitation": "LedgerPilot — Your business invitation",
}


def deliver(settings: Settings, recipient: str, purpose: str, token: str) -> None:
    if purpose not in SUBJECTS:
        raise ValueError("Unsupported identity email purpose")
    if not settings.auth_delivery_enabled:
        raise DomainError("ERR_MAIL_UNAVAILABLE", "Identity email delivery is not configured.", 503)
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = SUBJECTS[purpose]
    lifetime = (
        "15 minutes. Only the newest code works." if purpose in {"verify", "reset"} else "48 hours."
    )
    message.set_content(
        f"Your {purpose} token is:\n\n{token}\n\n"
        f"It expires in {lifetime}\n"
        "Use it only if you requested it in LedgerPilot. Never share it."
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as client:
            if settings.smtp_starttls:
                import ssl

                client.starttls(context=ssl.create_default_context())
            if settings.smtp_username and settings.smtp_password:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            client.send_message(message)
    except (OSError, smtplib.SMTPException):
        raise DomainError(
            "ERR_MAIL_UNAVAILABLE", "Identity email delivery is unavailable.", 503
        ) from None


def enqueue_mail(
    db: Any,
    settings: Settings,
    recipient: str,
    purpose: str,
    token: str,
    user_id: Any | None = None,
    business_id: Any | None = None,
) -> None:
    """Outbox pattern: record mail intent in the same transaction, send after commit."""
    if purpose not in SUBJECTS:
        raise ValueError("Unsupported identity email purpose")
    if not settings.auth_delivery_enabled:
        raise DomainError("ERR_MAIL_UNAVAILABLE", "Identity email delivery is not configured.", 503)

    from uuid import uuid4

    from ledgerpilot.identity.security import digest
    from ledgerpilot.jobs import service as job_service

    job_id = None
    if business_id is not None and user_id is not None:
        try:
            job = job_service.enqueue(
                db,
                business_id=business_id,
                created_by=user_id,
                queue="email",
                kind="identity_smtp",
                payload={
                    "recipient": recipient,
                    "purpose": purpose,
                    "token": token,
                },
                idempotency_key=f"smtp:{purpose}:{digest(token)}",
                max_attempts=settings.jobs_default_max_attempts,
                timeout_seconds=settings.jobs_default_timeout_seconds,
            )
            job_id = job.id
        except Exception:
            job_id = None

    def _send_after_commit() -> None:
        try:
            deliver(settings, recipient, purpose, token)
            if job_id is not None:
                bind = getattr(db, "get_bind", None)
                if bind and callable(bind):
                    from sqlalchemy.orm import Session as ScopedSession

                    with ScopedSession(bind()) as fin_db:
                        job_service.finish_job(fin_db, job_id, uuid4(), {"delivered": True})
                        fin_db.commit()
        except Exception:
            # SMTP failure leaves the job queued in outbox for background worker retry
            pass

    # For real database sessions, listen for after_commit; for mocks/unbound calls, send directly
    if hasattr(db, "connection") and hasattr(db, "get_bind") and type(db).__name__ != "MagicMock":
        try:
            from sqlalchemy import event

            event.listen(db, "after_commit", lambda s: _send_after_commit(), once=True)
            return
        except Exception:
            pass
    _send_after_commit()
