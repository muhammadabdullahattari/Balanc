"""Real SMTP delivery; disabled delivery fails explicitly, never returns tokens."""

import smtplib
from email.message import EmailMessage

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
