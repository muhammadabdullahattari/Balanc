"""Optional Google proof of identity; LedgerPilot still owns sessions, MFA and access."""

import secrets
from datetime import timedelta
from threading import BoundedSemaphore
from typing import Any
from uuid import uuid4

import requests
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import set_context
from ledgerpilot.identity import repository, service
from ledgerpilot.identity.models import GoogleChallenge, GoogleIdentity, User
from ledgerpilot.identity.schemas import GoogleCredentials
from ledgerpilot.identity.security import canonical_email, digest, hash_password, now, opaque
from ledgerpilot.shared.errors import DomainError

SLOTS = BoundedSemaphore(2)


def require_config(settings: Settings) -> str:
    if not settings.google_client_id:
        raise DomainError(
            "ERR_GOOGLE_UNAVAILABLE",
            "Google sign-in is not configured yet. Use email and password.",
            503,
        )
    return settings.google_client_id


def challenge(db: Session, settings: Settings, csrf: str) -> str:
    require_config(settings)
    nonce = opaque()
    db.execute(text("SELECT set_config('app.google_nonce_hash',:h,true)"), {"h": digest(nonce)})
    db.add(
        GoogleChallenge(
            nonce_hash=digest(nonce),
            csrf_hash=digest(csrf),
            expires_at=now() + timedelta(minutes=5),
        )
    )
    return nonce


def verified_claims(credential: str, audience: str) -> dict[str, Any]:
    if not SLOTS.acquire(blocking=False):
        raise DomainError("ERR_AUTH_BUSY", "Sign-in is busy. Please try again shortly.", 503)
    try:
        with requests.Session() as session:
            transport = GoogleRequest(session=session)

            def bounded_request(url: str, method: str = "GET", **kwargs: Any) -> Any:
                kwargs["timeout"] = 5
                return transport(url, method=method, **kwargs)

            claims = id_token.verify_oauth2_token(credential, bounded_request, audience=audience)  # type: ignore[no-untyped-call]
        if (
            claims.get("email_verified") is not True
            or not isinstance(claims.get("email"), str)
            or not isinstance(claims.get("sub"), str)
            or not 1 <= len(claims["sub"]) <= 255
        ):
            raise ValueError("Unverified identity")
        return dict(claims)
    except TransportError:
        raise DomainError(
            "ERR_GOOGLE_UNAVAILABLE",
            "Google sign-in is temporarily unavailable. Please try again.",
            503,
        ) from None
    except (ValueError, GoogleAuthError):
        raise DomainError(
            "ERR_GOOGLE_AUTH", "Google sign-in could not be verified. Please try again.", 401
        ) from None
    except requests.RequestException:
        raise DomainError(
            "ERR_GOOGLE_UNAVAILABLE",
            "Google sign-in is temporarily unavailable. Use email and password.",
            503,
        ) from None
    finally:
        SLOTS.release()


def consume_proof(
    db: Session, settings: Settings, data: GoogleCredentials, csrf: str
) -> dict[str, Any]:
    audience = require_config(settings)
    nonce = data.nonce.get_secret_value()
    db.execute(text("SELECT set_config('app.google_nonce_hash',:h,true)"), {"h": digest(nonce)})
    proof = db.scalar(
        select(GoogleChallenge).where(GoogleChallenge.nonce_hash == digest(nonce)).with_for_update()
    )
    if (
        not proof
        or proof.used_at
        or proof.expires_at <= now()
        or not secrets.compare_digest(proof.csrf_hash, digest(csrf))
    ):
        raise DomainError("ERR_GOOGLE_AUTH", "Google sign-in expired. Please try again.", 401)
    claims = verified_claims(data.credential.get_secret_value(), audience)
    if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(
        digest(claims["nonce"]), digest(nonce)
    ):
        raise DomainError("ERR_GOOGLE_AUTH", "Google sign-in expired. Please try again.", 401)
    proof.used_at = now()
    db.execute(text("SELECT set_config('app.google_subject',:s,true)"), {"s": claims["sub"]})
    return claims


def authenticate(
    db: Session, settings: Settings, data: GoogleCredentials, csrf: str
) -> tuple[User, bool]:
    claims = consume_proof(db, settings, data, csrf)
    mapping = db.get(GoogleIdentity, claims["sub"])
    if mapping:
        set_context(db, mapping.user_id)
        user = db.scalar(select(User).where(User.id == mapping.user_id).with_for_update())
        if user is None or user.suspended:
            raise DomainError("ERR_AUTH", "Unable to sign in. Please contact support.", 401)
    else:
        email = canonical_email(claims["email"])
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:email,0))"), {"email": email}
        )
        if repository.by_email(db, email):
            raise DomainError(
                "ERR_GOOGLE_LINK_REQUIRED",
                "Sign in with your password first, then link Google in Settings.",
                409,
            )
        if not (
            email.lower().endswith("@gmail.com")
            or isinstance(claims.get("hd"), str)
            and bool(claims["hd"])
        ):
            raise DomainError(
                "ERR_GOOGLE_EMAIL_VERIFICATION_REQUIRED",
                "Create and verify an email/password account first, then link Google in Settings.",
                409,
            )
        user = User(
            id=uuid4(),
            email=email,
            password_hash=hash_password(opaque()),
            verified=email.lower().endswith("@gmail.com") or bool(claims.get("hd")),
            suspended=False,
        )
        set_context(db, user.id)
        db.add(user)
        db.flush()
        db.add(GoogleIdentity(subject=claims["sub"], user_id=user.id))
        record(db, user.id, "identity.google_registered")
    if not user.verified:
        raise DomainError(
            "ERR_GOOGLE_EMAIL_VERIFICATION_REQUIRED",
            "Verify your LedgerPilot email before linking Google.",
            409,
        )
    mfa = service.consume_mfa(
        db,
        settings,
        user.id,
        data.mfa_code.get_secret_value() if data.mfa_code else None,
        data.recovery_code.get_secret_value() if data.recovery_code else None,
    )
    return user, mfa


def link(
    db: Session, settings: Settings, data: GoogleCredentials, csrf: str, user: User, mfa: bool
) -> None:
    if not mfa:
        raise DomainError(
            "ERR_MFA_REQUIRED", "Verify your authenticator before linking Google.", 403
        )
    claims = consume_proof(db, settings, data, csrf)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    if canonical_email(claims["email"]) != user.email:
        raise DomainError(
            "ERR_GOOGLE_EMAIL",
            "Choose the Google account with the same email as your LedgerPilot account.",
            409,
        )
    existing = db.scalar(
        select(GoogleIdentity).where(
            (GoogleIdentity.subject == claims["sub"]) | (GoogleIdentity.user_id == user.id)
        )
    )
    if existing:
        if existing.user_id == user.id and existing.subject == claims["sub"]:
            return
        raise DomainError("ERR_GOOGLE_LINK", "This Google account is already linked.", 409)
    db.add(GoogleIdentity(subject=claims["sub"], user_id=user.id))
    record(db, user.id, "identity.google_linked")
