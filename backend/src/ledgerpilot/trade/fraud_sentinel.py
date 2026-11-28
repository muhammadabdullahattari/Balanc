"""Vendor Fraud Sentinel, Tax ID Verification & Local Web Push VAPID Alerts (Phase N1 / Module 7).

Per user v1.1 scope:
- Local US EIN/TIN format & prefix validation + optional public EU VIES REST check.
- Local Web Push VAPID EC P-256 key generation (zero external push accounts).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.shared.errors import DomainError

_INVALID_EIN_PREFIXES = {
    "00",
    "07",
    "08",
    "09",
    "17",
    "18",
    "19",
    "28",
    "29",
    "49",
    "69",
    "70",
    "78",
    "79",
    "89",
    "96",
    "97",
}
_EU_COUNTRIES = {
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "EL",
    "ES",
    "FI",
    "FR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
}


def validate_tax_id(
    tax_id: str,
    *,
    country_code: str = "US",
    check_eu_vies_online: bool = False,
) -> dict[str, Any]:
    """Validate US EIN/TIN locally or EU VAT format (plus optional public EU VIES REST lookup)."""
    raw = (tax_id or "").strip().upper()
    cc = (country_code or "US").strip().upper()[:2]
    if not raw:
        return {
            "tax_id": "",
            "country_code": cc,
            "status": "missing",
            "valid": False,
            "detail": "Tax ID is blank.",
        }

    if cc == "US":
        digits = re.sub(r"[^0-9]", "", raw)
        if len(digits) != 9:
            return {
                "tax_id": raw,
                "country_code": "US",
                "status": "invalid",
                "valid": False,
                "detail": "US EIN must have 9 digits (XX-XXXXXXX).",
            }
        prefix = digits[:2]
        if prefix in _INVALID_EIN_PREFIXES:
            return {
                "tax_id": raw,
                "country_code": "US",
                "status": "invalid",
                "valid": False,
                "detail": f"US EIN prefix {prefix} is unassigned by the IRS.",
            }
        formatted = f"{digits[:2]}-{digits[2:]}"
        return {
            "tax_id": formatted,
            "country_code": "US",
            "status": "valid_format",
            "valid": True,
            "detail": "Valid IRS EIN format and campus prefix.",
        }

    # EU VAT check
    vat_clean = re.sub(r"[^A-Z0-9]", "", raw)
    prefix = vat_clean[:2] if len(vat_clean) >= 2 and vat_clean[:2].isalpha() else cc
    number = vat_clean[2:] if vat_clean.startswith(prefix) else vat_clean
    if prefix not in _EU_COUNTRIES or not re.match(r"^[A-Z0-9]{7,13}$", number):
        return {
            "tax_id": raw,
            "country_code": prefix,
            "status": "invalid",
            "valid": False,
            "detail": "VAT number does not match EU country format.",
        }

    if not check_eu_vies_online:
        return {
            "tax_id": f"{prefix}{number}",
            "country_code": prefix,
            "status": "valid_format",
            "valid": True,
            "detail": "Valid EU VAT format (offline check).",
        }

    try:
        resp = httpx.get(
            f"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{prefix}/vat/{number}",
            timeout=3.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            is_valid = bool(data.get("isValid"))
            return {
                "tax_id": f"{prefix}{number}",
                "country_code": prefix,
                "status": "valid" if is_valid else "invalid",
                "valid": is_valid,
                "detail": str(
                    data.get("name")
                    or ("Verified via EU VIES." if is_valid else "Rejected by EU VIES.")
                ),
            }
    except Exception:
        pass
    # Never report 'valid' on network failure — always 'unavailable'.
    return {
        "tax_id": f"{prefix}{number}",
        "country_code": prefix,
        "status": "unavailable",
        "valid": False,
        "detail": "EU VIES service unreachable; manual verification required.",
    }


def extract_bank_identifiers(invoice_text: str) -> dict[str, str]:
    """Extract IBAN, US ABA routing number, and account number hints from invoice text."""
    iban_m = re.search(r"\b([A-Z]{2}\d{2}[A-Z0-9]{11,30})\b", invoice_text.upper())
    routing_m = re.search(
        r"(?:routing|aba)\s*(?:no|#|number)?\s*[:\-]?\s*(\d{9})\b", invoice_text, re.I
    )
    acct_m = re.search(
        r"(?:account|acct)\s*(?:no|#|number)?\s*[:\-]?\s*(\d{6,17})\b", invoice_text, re.I
    )
    return {
        "iban": iban_m.group(1) if iban_m else "",
        "routing_number": routing_m.group(1) if routing_m else "",
        "account_number": acct_m.group(1) if acct_m else "",
    }


def _hash_bank_details(iban: str, routing: str, account: str) -> str:
    canonical = f"{iban.strip().upper()}|{routing.strip()}|{account.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_vendor_fraud_risk(
    *,
    vendor_name: str,
    invoice_text: str,
    invoice_amount: Decimal,
    historical_avg_amount: Decimal | None = None,
    known_bank_hash: str | None = None,
    sender_domain: str = "",
    vendor_domain: str = "",
    tax_id: str = "",
    country_code: str = "US",
) -> dict[str, Any]:
    """Compute weighted 0-100 fraud risk score; freeze payment automatically when score >= 70."""
    score = 0
    reasons: list[str] = []
    bank = extract_bank_identifiers(invoice_text)
    current_hash = (
        _hash_bank_details(bank["iban"], bank["routing_number"], bank["account_number"])
        if any(bank.values())
        else ""
    )

    # 1. Changed bank details (+55)
    if known_bank_hash and current_hash and known_bank_hash != current_hash:
        score += 55
        reasons.append("Bank account / IBAN on invoice differs from verified beneficiary details.")

    # 2. Lookalike / mismatched sender email domain (+30)
    sd = sender_domain.strip().lower()
    vd = vendor_domain.strip().lower()
    if sd and vd and sd != vd and not sd.endswith("." + vd):
        score += 30
        reasons.append(f"Sender domain ({sd}) does not match vendor domain ({vd}).")

    # 3. Urgent wire / secrecy language (+20)
    if re.search(
        r"\b(urgent\s+wire|new\s+bank\s+details|updated\s+remittance|do\s+not\s+call|confidential\s+transfer)\b",
        invoice_text,
        re.I,
    ):
        score += 20
        reasons.append("Invoice contains high-risk bank-change or urgent-wire phrasing.")

    # 4. Unusual amount (> 3x historical average) (+15)
    amt = abs(Decimal(str(invoice_amount))).quantize(Decimal("0.01"))
    if (
        historical_avg_amount is not None
        and historical_avg_amount > Decimal("0.00")
        and amt > (historical_avg_amount * Decimal("3"))
    ):
        score += 15
        reasons.append(
            f"Invoice amount ({amt}) exceeds 3x historical average ({historical_avg_amount})."
        )

    # 5. Invalid Tax ID (+15)
    tax_check: dict[str, Any] | None = None
    if tax_id:
        tax_check = validate_tax_id(tax_id, country_code=country_code)
        if not tax_check["valid"] and tax_check["status"] == "invalid":
            score += 15
            reasons.append(f"Tax ID validation failed: {tax_check['detail']}")

    score = min(100, score)
    frozen = score >= 70
    status = "frozen" if frozen else ("flagged" if score >= 35 else "clear")
    return {
        "vendor_name": vendor_name,
        "risk_score": score,
        "status": status,
        "payment_frozen": frozen,
        "extracted_bank": bank,
        "bank_hash": current_hash,
        "tax_check": tax_check,
        "reasons": reasons,
    }


def log_fraud_sentinel_event(
    db: Session,
    business_id: UUID,
    *,
    evaluation: dict[str, Any],
    vendor_id: UUID | None = None,
    invoice_id: UUID | None = None,
) -> dict[str, Any]:
    lid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.vendor_fraud_sentinel_logs
                (id, business_id, vendor_id, invoice_id, anomaly_type, risk_score,
                 details, status)
            VALUES
                (:id, :bid, :vid, :iid, :atype, :score, CAST(:details AS jsonb), :st)
            """
        ),
        {
            "id": lid,
            "bid": business_id,
            "vid": vendor_id,
            "iid": invoice_id,
            "atype": "bank_change_or_fraud_risk"
            if evaluation["payment_frozen"]
            else "fraud_screen",
            "score": int(evaluation["risk_score"]),
            "details": json.dumps(evaluation),
            "st": evaluation["status"],
        },
    )
    return {"id": str(lid), **evaluation}


def generate_local_vapid_keypair() -> dict[str, str]:
    """Generate a local Web Push VAPID EC P-256 keypair with zero external accounts."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    priv_num = private_key.private_numbers().private_value.to_bytes(32, "big")
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode("ascii").rstrip("=")
    priv_b64 = base64.urlsafe_b64encode(priv_num).decode("ascii").rstrip("=")
    return {
        "algorithm": "ES256-P256",
        "public_key": pub_b64,
        "private_key": priv_b64,
    }


def build_vapid_alert_payload(
    *,
    title: str,
    body: str,
    risk_score: int,
    vapid_private_key_b64: str,
    vapid_public_key_b64: str,
    subject: str = "mailto:security@ledgerpilot.local",
) -> dict[str, Any]:
    """Create a signed Web Push VAPID notification payload for owner phone alerts."""
    padded = vapid_private_key_b64 + "=" * (-len(vapid_private_key_b64) % 4)
    priv_int = int.from_bytes(base64.urlsafe_b64decode(padded.encode("ascii")), "big")
    private_key = ec.derive_private_key(priv_int, ec.SECP256R1())
    header = (
        base64.urlsafe_b64encode(
            json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    claims = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "aud": "https://push.ledgerpilot.local",
                    "exp": int((datetime.now(UTC) + timedelta(hours=12)).timestamp()),
                    "sub": subject,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    sig_der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.urlsafe_b64encode(sig_der).decode("ascii").rstrip("=")
    return {
        "vapid_authorization": f"vapid t={header}.{claims}.{sig_b64}, k={vapid_public_key_b64}",
        "notification": {
            "title": title,
            "body": body,
            "risk_score": risk_score,
            "tag": "ledgerpilot-fraud-alert",
        },
    }


def register_push_subscription(
    db: Session,
    *,
    business_id: UUID,
    user_id: UUID,
    endpoint: str,
    p256dh: str,
    auth_secret: str,
) -> dict[str, Any]:
    if not endpoint.startswith("https://"):
        raise DomainError("ERR_VALIDATION", "Push subscription endpoint must use https://.", 422)
    sid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.push_subscriptions
                (id, business_id, user_id, endpoint, p256dh, auth_secret, active)
            VALUES (:id, :bid, :uid, :ep, :p256, :auth, TRUE)
            ON CONFLICT (business_id, user_id, endpoint)
            DO UPDATE SET
                p256dh = EXCLUDED.p256dh,
                auth_secret = EXCLUDED.auth_secret,
                active = TRUE
            """
        ),
        {
            "id": sid,
            "bid": business_id,
            "uid": user_id,
            "ep": endpoint[:500],
            "p256": p256dh[:255],
            "auth": auth_secret[:255],
        },
    )
    return {"id": str(sid), "endpoint": endpoint, "active": True}
