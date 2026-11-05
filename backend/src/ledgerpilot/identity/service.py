"""Verified identity lifecycle and durable session revocation."""

import logging
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

import pyotp
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import set_context
from ledgerpilot.identity import repository
from ledgerpilot.identity.mail import deliver
from ledgerpilot.identity.models import (
    ActionToken,
    AuthSession,
    MFAFactor,
    RecoveryCode,
    RefreshToken,
    User,
)
from ledgerpilot.identity.schemas import Credentials, Registration
from ledgerpilot.identity.security import (
    canonical_email,
    digest,
    encode_access,
    encryption,
    hash_password,
    normalized_password,
    now,
    opaque,
    totp_counter,
    verify_password,
)
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import emit
from ledgerpilot.tenancy.models import Membership


def register(db: Session, settings: Settings, data: Registration, csrf: str) -> None:
    email = canonical_email(data.email)
    password = normalized_password(data.password.get_secret_value(), email)
    # Serialize registration/resend for an address, including its first insertion.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:email,0))"), {"email": email})
    existing = repository.by_email(db, email)
    if existing:
        new_hash = hash_password(password)
        set_context(db, existing.id)
        existing = db.scalar(
            select(User)
            .where(User.id == existing.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing and not existing.verified and not existing.suspended:
            existing.password_hash = new_hash
            existing.verification_csrf_hash = digest(csrf)
            # A restarted signup must retire old codes even if SMTP is offline.
            db.execute(
                update(ActionToken)
                .where(
                    ActionToken.user_id == existing.id,
                    ActionToken.purpose == "verify",
                    ActionToken.used_at.is_(None),
                )
                .values(used_at=now())
            )
            verification_delivery(db, settings, existing.id, email)
        return
    user_id = uuid4()
    set_context(db, user_id)
    inserted = db.execute(
        insert(User)
        .values(
            id=user_id,
            email=email,
            password_hash=hash_password(password),
            verified=False,
            verification_csrf_hash=digest(csrf),
            suspended=False,
        )
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User.id)
    ).scalar_one_or_none()
    if inserted is None:
        return
    verification_delivery(db, settings, user_id, email)
    record(db, user_id, "identity.registered")


def verification_delivery(db: Session, settings: Settings, user_id: UUID, email: str) -> None:
    """Keep the pending identity while rolling back only unsent token issuance.

    begin_nested flushes pending identity/binding changes first. Delivery failure
    restores token/attempt state; the existing browser-bound resend flow retries.
    This is not a durable mail queue and never stores or logs a raw action token.
    """
    try:
        with db.begin_nested():
            send_action(db, settings, user_id, email, "verify")
    except DomainError as error:
        if error.code != "ERR_MAIL_UNAVAILABLE":
            raise
        emit(
            "identity.delivery_unavailable", level=logging.WARNING, user_id=user_id, code=error.code
        )


def send_action(db: Session, settings: Settings, user_id: UUID, email: str, purpose: str) -> None:
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise DomainError("ERR_AUTH", "Please sign in again.", 401)
    instant = now()
    if purpose == "verify":
        if user.verification_blocked_until and user.verification_blocked_until > instant:
            wait = max(1, int((user.verification_blocked_until - instant).total_seconds()))
            raise DomainError(
                "ERR_VERIFICATION_LIMIT",
                "Too many verification requests. Try again in 24 hours, "
                "or use the newest code already sent.",
                429,
                retry_after=wait,
            )
        if not user.verification_started_at or user.verification_started_at <= instant - timedelta(
            hours=24
        ):
            user.verification_attempts = 0
            user.verification_started_at = instant
            user.verification_blocked_until = None
        user.verification_attempts += 1
        if user.verification_attempts >= 3:
            user.verification_blocked_until = instant + timedelta(hours=24)
            record(db, user_id, "identity.verification_limited")
    # Only the newest token of this purpose can be consumed.
    db.execute(
        update(ActionToken)
        .where(
            ActionToken.user_id == user_id,
            ActionToken.purpose == purpose,
            ActionToken.used_at.is_(None),
        )
        .values(used_at=instant)
    )
    token = opaque()
    db.add(
        ActionToken(
            token_hash=digest(token),
            user_id=user_id,
            purpose=purpose,
            expires_at=now() + timedelta(minutes=15),
        )
    )
    deliver(settings, email, purpose, token)


def request_action(db: Session, settings: Settings, email: str, purpose: str, csrf: str) -> None:
    user = repository.by_email(db, canonical_email(email))
    if user and not user.suspended and (purpose == "reset" or not user.verified):
        set_context(db, user.id)
        user = db.scalar(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            return
        if purpose == "verify" and (
            not user.verification_csrf_hash
            or not secrets.compare_digest(user.verification_csrf_hash, digest(csrf))
        ):
            return
        if purpose == "verify":
            verification_delivery(db, settings, user.id, user.email)
        else:
            send_action(db, settings, user.id, user.email, purpose)


def consume_action(db: Session, raw: str, purpose: str, csrf: str = "") -> User:
    token = repository.by_token(db, digest(raw), purpose)
    if not token or token.used_at or token.expires_at <= now():
        raise DomainError("ERR_LINK_EXPIRED", "Token is invalid or expired.", 410)
    user = db.get(User, token.user_id)
    if not user or user.suspended:
        raise DomainError("ERR_AUTH", "Authentication failed.", 401)
    if purpose == "verify" and (
        not user.verification_csrf_hash
        or not secrets.compare_digest(user.verification_csrf_hash, digest(csrf))
    ):
        raise DomainError(
            "ERR_VERIFICATION_SESSION",
            "Use the browser where you signed up, or restart sign-up here to request a new code.",
            410,
        )
    token.used_at = now()
    return user


def verify(db: Session, raw: str, csrf: str) -> None:
    user = consume_action(db, raw, "verify", csrf)
    user.verified = True
    user.verification_csrf_hash = None
    user.verification_attempts = 0
    user.verification_started_at = None
    user.verification_blocked_until = None
    record(db, user.id, "identity.verified")


def revoke_all(db: Session, user_id: UUID) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now())
    )


def reset_password(db: Session, raw: str, password: str) -> None:
    user = consume_action(db, raw, "reset")
    user.password_hash = hash_password(normalized_password(password, user.email))
    # Reset tokens cannot change the password again after a later reset.
    db.execute(
        update(ActionToken)
        .where(ActionToken.user_id == user.id, ActionToken.used_at.is_(None))
        .values(used_at=now())
    )
    revoke_all(db, user.id)
    record(db, user.id, "identity.password_reset")


def consume_mfa(
    db: Session, settings: Settings, user_id: UUID, code: str | None, recovery: str | None
) -> bool:
    factor = db.scalar(select(MFAFactor).where(MFAFactor.user_id == user_id).with_for_update())
    if not factor or not factor.verified:
        return False
    if recovery:
        item = db.scalar(
            select(RecoveryCode)
            .where(RecoveryCode.user_id == user_id, RecoveryCode.code_hash == digest(recovery))
            .with_for_update()
        )
        if not item or item.used_at:
            raise DomainError(
                "ERR_MFA_CODE", "The recovery code is incorrect or already used.", 401
            )
        item.used_at = now()
        revoke_all(db, user_id)
        record(db, user_id, "identity.recovery_code_used")
    elif code:
        secret = encryption(settings).decrypt(factor.encrypted_secret.encode()).decode()
        factor.last_counter = totp_counter(secret, code, factor.last_counter)
    else:
        raise DomainError("ERR_MFA_REQUIRED", "A valid MFA or recovery code is required.", 401)
    return True


def issue_session(db: Session, settings: Settings, user: User, mfa: bool) -> tuple[str, str, str]:
    csrf, refresh = opaque(), opaque()
    family = AuthSession(
        user_id=user.id,
        password_version=user.password_version,
        csrf_hash=digest(csrf),
        mfa=mfa,
        expires_at=now() + timedelta(days=7),
        last_seen_at=now(),
    )
    db.add(family)
    db.flush()
    db.add(RefreshToken(token_hash=digest(refresh), session_id=family.id, user_id=user.id))
    record(db, user.id, "session.created", resource_id=family.id)
    return encode_access(settings, user.id, family.id), refresh, csrf


def login(db: Session, settings: Settings, data: Credentials) -> tuple[str, str, str]:
    user = repository.by_email(db, canonical_email(data.email))
    valid = verify_password(user.password_hash if user else None, data.password.get_secret_value())
    if not valid or not user:
        raise DomainError("ERR_AUTH", "Authentication failed.", 401)
    # Only reveal account state after the password was proved correct.
    if user.suspended:
        raise DomainError(
            "ERR_ACCOUNT_UNAVAILABLE", "This account is unavailable. Contact support.", 403
        )
    if not user.verified:
        raise DomainError(
            "ERR_EMAIL_UNVERIFIED",
            "Verify your email before signing in. "
            "Use the browser where you signed up to resend the code.",
            403,
        )
    set_context(db, user.id)
    mfa = consume_mfa(
        db,
        settings,
        user.id,
        data.mfa_code.get_secret_value() if data.mfa_code else None,
        data.recovery_code.get_secret_value() if data.recovery_code else None,
    )
    return issue_session(db, settings, user, mfa)


def valid_family(db: Session, family: AuthSession | None) -> User:
    user = db.get(User, family.user_id) if family else None
    if not family or not user:
        raise DomainError("ERR_AUTH", "Authentication required.", 401)
    if family.revoked_at:
        raise DomainError("ERR_SESSION_REVOKED", "Your session was ended. Sign in again.", 401)
    if family.expires_at <= now():
        raise DomainError("ERR_SESSION_EXPIRED", "Your session expired. Sign in again.", 401)
    if user.suspended or not user.verified:
        raise DomainError(
            "ERR_SESSION_UNAVAILABLE",
            "Your account cannot use this session. Sign in again or contact support.",
            401,
        )
    if family.password_version != user.password_version:
        raise DomainError("ERR_SESSION_CHANGED", "Your password changed. Sign in again.", 401)
    privileged = db.scalar(
        select(Membership.user_id)
        .where(
            Membership.user_id == user.id,
            Membership.active.is_(True),
            Membership.role.in_(["owner", "accountant"]),
        )
        .limit(1)
    )
    if privileged and family.last_seen_at < now() - timedelta(minutes=15):
        raise DomainError(
            "ERR_SESSION_IDLE", "Your session expired through inactivity. Sign in again.", 401
        )
    family.last_seen_at = now()
    return user


def refresh(db: Session, settings: Settings, raw: str, csrf: str) -> tuple[str, str, str]:
    token = repository.refresh_record(db, digest(raw))
    family = db.get(AuthSession, token.session_id) if token else None
    # Validate session-bound CSRF before applying replay revocation.
    if not family or not secrets.compare_digest(family.csrf_hash, digest(csrf)):
        raise DomainError("ERR_CSRF", "CSRF check failed.", 403)
    if token and token.used_at:
        family.revoked_at = now()
        record(db, family.user_id, "session.refresh_replay", resource_id=family.id)
        db.commit()  # Revocation must survive the rejected request's rollback.
        raise DomainError("ERR_SESSION_REVOKED", "Your session was ended. Sign in again.", 401)
    user = valid_family(db, family)
    if not token:
        raise DomainError("ERR_AUTH", "Authentication required.", 401)
    token.used_at = now()
    replacement = opaque()
    db.add(RefreshToken(token_hash=digest(replacement), session_id=family.id, user_id=user.id))
    # Keep CSRF stable within the family so concurrent replay detection is reliable.
    return encode_access(settings, user.id, family.id), replacement, csrf


def enroll_mfa(db: Session, settings: Settings, user_id: UUID) -> dict[str, str]:
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise DomainError("ERR_AUTH", "Please sign in again.", 401)
    factor = db.scalar(
        select(MFAFactor)
        .where(MFAFactor.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if factor and factor.verified:
        raise DomainError("ERR_MFA_ENROLLED", "MFA is already enrolled.", 409)
    secret = pyotp.random_base32()
    encrypted = encryption(settings).encrypt(secret.encode()).decode()
    if factor:
        factor.encrypted_secret = encrypted
        factor.last_counter = -1
    else:
        db.add(MFAFactor(user_id=user_id, encrypted_secret=encrypted))
    record(db, user_id, "mfa.enrollment_started")
    return {
        "secret": secret,
        "provisioning_uri": pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name="LedgerPilot"
        ),
    }


def confirm_mfa(db: Session, settings: Settings, family: AuthSession, code: str) -> list[str]:
    factor = db.scalar(
        select(MFAFactor).where(MFAFactor.user_id == family.user_id).with_for_update()
    )
    if not factor or factor.verified:
        raise DomainError("ERR_MFA_ENROLLED", "MFA enrollment is unavailable.", 409)
    secret = encryption(settings).decrypt(factor.encrypted_secret.encode()).decode()
    factor.last_counter = totp_counter(secret, code, factor.last_counter)
    factor.verified = True
    family.mfa = True
    codes = [opaque() for _ in range(10)]
    for code in codes:
        db.add(RecoveryCode(user_id=family.user_id, code_hash=digest(code)))
    record(db, family.user_id, "mfa.enrolled")
    return codes
