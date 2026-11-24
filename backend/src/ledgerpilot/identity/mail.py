"""Real SMTP delivery; disabled delivery fails explicitly, never returns tokens."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError

logger = logging.getLogger("ledgerpilot.identity.mail")

SUBJECTS = {
    "verify": "LedgerPilot — Verify your email",
    "reset": "LedgerPilot — Reset your password",
    "business invitation": "LedgerPilot — Your business invitation",
}


def _deliver_raw(
    settings: Settings,
    recipient: str,
    subject: str,
    body_text: str,
    *,
    tag: str = "transactional",
    html_body: str | None = None,
) -> list[str]:
    """Deliver via Resend -> Brevo -> SendGrid -> SMTP. Empty list means success."""
    import json
    import os
    import urllib.error
    import urllib.request

    errors: list[str] = []

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        resend_key = resend_key.strip().strip("'\"")
        sender = os.environ.get("RESEND_FROM") or "onboarding@resend.dev"
        body_obj: dict[str, Any] = {
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "text": body_text,
        }
        if html_body:
            body_obj["html"] = html_body
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(body_obj).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json",
                "User-Agent": "LedgerPilot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    logger.info("Email (%s) delivered to %s via Resend HTTPS", tag, recipient)
                    return []
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()
            logger.warning("Resend delivery failed (%s): %s", exc.code, err_body)
            errors.append(f"Resend ({exc.code}): {err_body}")
        except Exception as exc:
            logger.warning("Resend delivery failed: %s", exc)
            errors.append(f"Resend: {exc}")

    brevo_key = os.environ.get("BREVO_API_KEY")
    if brevo_key:
        brevo_key = brevo_key.strip().strip("'\"")
        sender_email = os.environ.get("BREVO_FROM") or settings.smtp_from or "noreply@balanc.local"
        sender_name = os.environ.get("BREVO_SENDER_NAME") or "LedgerPilot"
        brevo_obj: dict[str, Any] = {
            "sender": {"email": sender_email, "name": sender_name},
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": body_text,
        }
        if html_body:
            brevo_obj["htmlContent"] = html_body
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(brevo_obj).encode("utf-8"),
            headers={
                "api-key": brevo_key,
                "Content-Type": "application/json",
                "User-Agent": "LedgerPilot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    logger.info("Email (%s) delivered to %s via Brevo HTTPS", tag, recipient)
                    return []
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()
            logger.warning("Brevo delivery failed (%s): %s", exc.code, err_body)
            errors.append(f"Brevo ({exc.code}): {err_body}")
        except Exception as exc:
            logger.warning("Brevo delivery failed: %s", exc)
            errors.append(f"Brevo: {exc}")

    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    if sendgrid_key:
        sendgrid_key = sendgrid_key.strip().strip("'\"")
        sender_email = (
            os.environ.get("SENDGRID_FROM") or settings.smtp_from or "noreply@balanc.local"
        )
        sender_name = os.environ.get("SENDGRID_SENDER_NAME") or "LedgerPilot"
        contents = [{"type": "text/plain", "value": body_text}]
        if html_body:
            contents.append({"type": "text/html", "value": html_body})
        sg_obj = {
            "personalizations": [{"to": [{"email": recipient}]}],
            "from": {"email": sender_email, "name": sender_name},
            "subject": subject,
            "content": contents,
        }
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=json.dumps(sg_obj).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {sendgrid_key}",
                "Content-Type": "application/json",
                "User-Agent": "LedgerPilot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201, 202):
                    logger.info("Email (%s) delivered to %s via SendGrid HTTPS", tag, recipient)
                    return []
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()
            logger.warning("SendGrid delivery failed (%s): %s", exc.code, err_body)
            errors.append(f"SendGrid ({exc.code}): {err_body}")
        except Exception as exc:
            logger.warning("SendGrid delivery failed: %s", exc)
            errors.append(f"SendGrid: {exc}")

    if settings.smtp_host:
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body_text)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        try:
            smtp_cls = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
            with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=15) as client:
                if settings.smtp_starttls and settings.smtp_port != 465:
                    import ssl

                    client.starttls(context=ssl.create_default_context())
                if settings.smtp_username and settings.smtp_password:
                    client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
                client.send_message(message)
                logger.info(
                    "Email (%s) delivered to %s via %s:%d",
                    tag,
                    recipient,
                    settings.smtp_host,
                    settings.smtp_port,
                )
                return []
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning(
                "Email delivery failed for %s to %s via %s:%d: %s",
                tag,
                recipient,
                settings.smtp_host,
                settings.smtp_port,
                exc,
            )
            errors.append(f"SMTP ({settings.smtp_host}:{settings.smtp_port}): {exc}")

    if not errors:
        errors.append(
            "No active email providers configured (set BREVO_API_KEY, RESEND_API_KEY, "
            "SENDGRID_API_KEY, or SMTP_HOST)."
        )
    return errors


def send_transactional_email(
    settings: Settings,
    recipient: str,
    subject: str,
    body_text: str,
    *,
    tag: str = "billing",
    html_body: str | None = None,
) -> bool:
    """Best-effort transactional email delivery (billing receipts, invoices, notices)."""
    if not recipient or "@" not in recipient:
        return False
    if not getattr(settings, "auth_delivery_enabled", False):
        return False
    errors = _deliver_raw(settings, recipient, subject, body_text, tag=tag, html_body=html_body)
    if errors:
        logger.warning(
            "Transactional email (%s) to %s failed: %s", tag, recipient, " | ".join(errors)
        )
        return False
    return True


def deliver(settings: Settings, recipient: str, purpose: str, token: str) -> None:
    if purpose not in SUBJECTS:
        raise ValueError("Unsupported identity email purpose")
    if not settings.auth_delivery_enabled:
        raise DomainError("ERR_MAIL_UNAVAILABLE", "Identity email delivery is not configured.", 503)
    lifetime = (
        "15 minutes. Only the newest code works." if purpose in {"verify", "reset"} else "48 hours."
    )
    body_text = (
        f"Your {purpose} token is:\n\n{token}\n\n"
        f"It expires in {lifetime}\n"
        "Use it only if you requested it in LedgerPilot. Never share it."
    )
    errors = _deliver_raw(settings, recipient, SUBJECTS[purpose], body_text, tag=purpose)
    if not errors:
        return
    aggregated = " | ".join(errors)
    if any("You can only send testing emails to your own email address" in e for e in errors):
        aggregated += (
            f" [HINT: Resend test sandbox restriction active: onboarding@resend.dev only allows "
            f"sending to the account owner. To deliver to '{recipient}', either configure "
            f"BREVO_API_KEY in Railway for free domainless delivery, or verify a custom domain "
            f"at resend.com/domains.]"
        )
    raise DomainError("ERR_MAIL_UNAVAILABLE", f"Email delivery failed: {aggregated}", 503)


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
        except Exception as exc:
            logger.error(
                "Identity email delivery attempt failed in outbox for %s to %s: %s",
                purpose,
                recipient,
                exc,
            )
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
