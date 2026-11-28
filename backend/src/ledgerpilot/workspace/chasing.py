"""Email-first missing-receipt & document chasing with signed upload links (Phase L1 / Module 1).

Per user v1.1 scope: starts with Email reminders (no SMS/Twilio, no Playwright vendor scraping).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.auto_post import should_auto_post


def _token_secret(settings: Settings) -> bytes:
    return hashlib.sha256(
        f"chase-upload:{settings.session_signing_key.get_secret_value()}".encode()
    ).digest()


def create_signed_upload_token(
    settings: Settings,
    *,
    business_id: UUID,
    chase_event_id: UUID,
    ttl_days: int = 7,
) -> tuple[str, datetime]:
    """Create a URL-safe HMAC-SHA256 signed upload token valid for ttl_days (default 7)."""
    expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
    payload = {
        "b": str(business_id),
        "c": str(chase_event_id),
        "exp": int(expires_at.timestamp()),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_token_secret(settings), b64.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{b64}.{sig}", expires_at


def verify_signed_upload_token(settings: Settings, token: str) -> tuple[UUID, UUID]:
    """Verify HMAC signature and expiry of a chase upload token."""
    if "." not in token:
        raise DomainError("ERR_INVALID_TOKEN", "Upload link is invalid.", 400)
    b64, sig = token.rsplit(".", 1)
    expected = hmac.new(_token_secret(settings), b64.encode("ascii"), hashlib.sha256).hexdigest()[
        :32
    ]
    if not hmac.compare_digest(sig, expected):
        raise DomainError("ERR_INVALID_TOKEN", "Upload link signature is invalid.", 403)
    padded = b64 + "=" * (-len(b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        exp = int(payload["exp"])
        bid = UUID(payload["b"])
        cid = UUID(payload["c"])
    except Exception as exc:
        raise DomainError("ERR_INVALID_TOKEN", "Upload link payload is malformed.", 400) from exc
    if datetime.now(UTC).timestamp() > exp:
        raise DomainError("ERR_TOKEN_EXPIRED", "This upload link has expired.", 410)
    return bid, cid


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_document_chase(
    db: Session,
    settings: Settings,
    business_id: UUID,
    *,
    recipient_email: str,
    target_entity_type: str = "bank_transaction",
    target_entity_id: UUID | None = None,
    channel: str = "email",
    transaction_summary: str = "",
) -> dict[str, Any]:
    """Create a document chase event with a 7-day single-use upload link (Email first)."""
    if channel not in {"email", "slack", "whatsapp"}:
        raise DomainError(
            "ERR_VALIDATION",
            "Channel must be 'email', 'slack', or 'whatsapp' (SMS is disabled).",
            422,
        )
    if not recipient_email or "@" not in recipient_email:
        raise DomainError("ERR_VALIDATION", "A valid recipient email address is required.", 422)

    cid = uuid4()
    entity_id = target_entity_id or uuid4()
    token, expires_at = create_signed_upload_token(
        settings, business_id=business_id, chase_event_id=cid, ttl_days=7
    )
    upload_path = f"/u/{token}"

    db.execute(
        text(
            """
            INSERT INTO app.document_chase_events
                (id, business_id, target_entity_type, target_entity_id, channel,
                 recipient, status, attempt_count, upload_token_hash,
                 upload_token_expires_at, sent_at)
            VALUES
                (:id, :bid, :etype, :eid, :ch, :rec, 'sent', 1, :thash, :exp, now())
            """
        ),
        {
            "id": cid,
            "bid": business_id,
            "etype": target_entity_type[:40],
            "eid": entity_id,
            "ch": channel,
            "rec": recipient_email.strip().lower()[:255],
            "thash": token_digest(token),
            "exp": expires_at,
        },
    )

    email_sent = False
    try:
        from ledgerpilot.identity.mail import _deliver_raw

        subject = "Receipt needed for your recent business transaction"
        body = (
            f"Hi,\n\nWe need the receipt or invoice for: "
            f"{transaction_summary or 'recent transaction'}.\n"
            f"Upload it securely (no login required, valid 7 days): {upload_path}\n\n"
            "Thank you,\nLedgerPilot Autonomous Finance Office"
        )
        errs = _deliver_raw(settings, recipient_email.strip().lower(), subject, body)
        email_sent = len(errs) == 0
    except Exception:
        email_sent = False

    return {
        "id": str(cid),
        "target_entity_type": target_entity_type,
        "target_entity_id": str(entity_id),
        "channel": channel,
        "recipient": recipient_email.strip().lower(),
        "status": "sent",
        "attempt_count": 1,
        "upload_path": upload_path,
        "upload_token": token,
        "upload_token_expires_at": expires_at.isoformat(),
        "email_dispatched": email_sent,
    }


def list_document_chases(db: Session, business_id: UUID) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            text(
                """
                SELECT id, target_entity_type, target_entity_id, channel, recipient,
                       status, attempt_count, upload_token_expires_at,
                       received_evidence_id, sent_at, resolved_at
                FROM app.document_chase_events
                WHERE business_id = :bid
                ORDER BY sent_at DESC
                LIMIT 200
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": str(r["id"]),
            "target_entity_type": r["target_entity_type"],
            "target_entity_id": str(r["target_entity_id"]),
            "channel": r["channel"],
            "recipient": r["recipient"],
            "status": r["status"],
            "attempt_count": int(r["attempt_count"]),
            "upload_token_expires_at": r["upload_token_expires_at"].isoformat()
            if r["upload_token_expires_at"]
            else None,
            "received_evidence_id": str(r["received_evidence_id"])
            if r["received_evidence_id"]
            else None,
            "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        }
        for r in rows
    ]


def match_chased_receipt(
    db: Session | None,
    business_id: UUID,
    *,
    expected_amount: Decimal,
    extracted_amount: Decimal,
    expected_vendor: str,
    extracted_vendor: str,
) -> dict[str, Any]:
    """Score a chased receipt against its bank transaction; auto-link if >95% confidence."""
    exp_amt = abs(expected_amount).quantize(Decimal("0.01"))
    ext_amt = abs(extracted_amount).quantize(Decimal("0.01"))
    amt_diff = abs(exp_amt - ext_amt)
    amt_score = (
        Decimal("1.00")
        if amt_diff == Decimal("0.00")
        else (Decimal("0.90") if amt_diff <= Decimal("0.05") else Decimal("0.20"))
    )
    v1 = "".join(c for c in expected_vendor.lower() if c.isalnum())
    v2 = "".join(c for c in extracted_vendor.lower() if c.isalnum())
    vendor_score = Decimal("1.00") if v1 and v2 and (v1 in v2 or v2 in v1) else Decimal("0.50")
    confidence = ((amt_score * Decimal("0.75")) + (vendor_score * Decimal("0.25"))).quantize(
        Decimal("0.01")
    )
    auto_linked = confidence > Decimal("0.95")
    auto_posted = False
    if auto_linked and db is not None:
        allowed, _ = should_auto_post(db, business_id, "receipt_auto_match", exp_amt)
        auto_posted = allowed
    return {
        "confidence": str(confidence),
        "auto_linked": auto_linked,
        "auto_posted": auto_posted,
        "amount_difference": str(amt_diff),
    }
