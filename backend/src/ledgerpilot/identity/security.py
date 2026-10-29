"""Password, token and one-time MFA primitives; no custom JWT implementation."""

import hashlib
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from threading import BoundedSemaphore
from uuid import UUID

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from argon2.low_level import Type
from cryptography.fernet import Fernet
from email_validator import EmailNotValidError, validate_email

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError

PASSWORDS = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16, type=Type.ID
)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
PASSWORD_SLOTS = BoundedSemaphore(2)  # At most 128 MiB of active Argon2 memory per process.
COMMON_PASSWORDS = {
    "passwordpassword",
    "password123456789",
    "123456789012345",
    "qwertyuiopasdfgh",
    "letmeinletmeinletmein",
    "correcthorsebatterystaple",
}
COMMON_PASSWORDS.update(
    line.casefold()
    for line in files("ledgerpilot.identity")
    .joinpath("common-passwords.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)


def now() -> datetime:
    return datetime.now(UTC)


def opaque() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_email(value: str) -> str:
    try:
        parsed = validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False)
        address = f"{parsed.local_part}@{parsed.ascii_domain}"
        if len(address) > 254 or len(parsed.local_part) > 64:
            raise ValueError
        return address
    except (EmailNotValidError, ValueError):
        raise DomainError("ERR_EMAIL", "Email address is invalid or unsupported.") from None


def normalized_password(value: str, email: str = "") -> str:
    result = unicodedata.normalize("NFC", value)
    local = email.partition("@")[0].casefold()
    if (
        not 15 <= len(result) <= 128
        or result.casefold() in COMMON_PASSWORDS
        or (local and result.casefold() in {local, local * 2, local + "123456789"})
    ):
        raise DomainError(
            "ERR_PASSWORD", "Use 15–128 characters and avoid known compromised passwords."
        )
    return result


def verify_password(encoded: str | None, value: str) -> bool:
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise DomainError("ERR_AUTH_BUSY", "Authentication is temporarily busy.", 503)
    try:
        valid = PASSWORDS.verify(encoded or DUMMY_HASH, unicodedata.normalize("NFC", value))
        return bool(valid and encoded)
    except VerificationError:
        return False
    finally:
        PASSWORD_SLOTS.release()


def hash_password(value: str) -> str:
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise DomainError("ERR_AUTH_BUSY", "Authentication is temporarily busy.", 503)
    try:
        return PASSWORDS.hash(value)
    finally:
        PASSWORD_SLOTS.release()


def encryption(settings: Settings) -> Fernet:
    if not settings.mfa_encryption_key:
        raise DomainError("ERR_CONFIGURATION", "MFA encryption is not configured.", 503)
    return Fernet(settings.mfa_encryption_key.get_secret_value().encode())


def encode_access(settings: Settings, user_id: UUID, session_id: UUID) -> str:
    issued = now()
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": issued,
            "nbf": issued,
            "exp": issued + timedelta(minutes=10),
            "jti": opaque(),
        },
        settings.session_signing_key.get_secret_value(),
        algorithm="HS256",
        headers={"kid": settings.jwt_key_id},
    )


def decode_access(settings: Settings, token: str) -> tuple[UUID, UUID]:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid == settings.jwt_key_id:
            key = settings.session_signing_key
        elif settings.session_previous_signing_key and kid == settings.session_previous_key_id:
            key = settings.session_previous_signing_key
        else:
            raise ValueError
        claims = jwt.decode(
            token,
            key.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "sid", "iss", "aud", "iat", "nbf", "exp", "jti"]},
        )
        if claims["exp"] - claims["iat"] > 600:
            raise ValueError
        return UUID(claims["sub"]), UUID(claims["sid"])
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise DomainError("ERR_AUTH", "Authentication required.", 401) from None


def totp_counter(secret: str, code: str, previous: int) -> int:
    # A single adjacent step tolerates transmission/clock skew; counters are one-use.
    instant = now()
    counter = int(instant.timestamp()) // 30
    for candidate in (counter, counter - 1, counter + 1):
        if candidate > previous and secrets.compare_digest(
            pyotp.TOTP(secret).at(candidate * 30), code
        ):
            return candidate
    raise DomainError("ERR_AUTH", "Authentication failed.", 401)
