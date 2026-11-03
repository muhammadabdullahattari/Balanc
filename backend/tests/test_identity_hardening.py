"""Identity hardening: real DB/SMTP; Google certificate transport is isolated test input."""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from test_phase2_integration import (
    ORIGIN,
    database_settings,  # noqa: F401
    mail_token,
    new_identity,
)

from ledgerpilot.identity import google
from ledgerpilot.identity.security import normalized_password
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.main import create_app
from ledgerpilot.shared.errors import DomainError

# ruff: noqa: F811 -- shared real database fixture
AUDIENCE = "synthetic-client.apps.googleusercontent.com"
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1", reason="Requires PostgreSQL/Mailpit"
)


@pytest.mark.parametrize(
    "value",
    [
        "lowercase phrase 123!",
        "UPPERCASE PHRASE 123!",
        "No digits in phrase!",
        "No special phrase 123",
        "Short1!",
        "Spaces are not special 1",
    ],
)
def test_password_requires_all_requested_categories(value: str) -> None:
    with pytest.raises(DomainError) as error:
        normalized_password(value)
    assert error.value.code == "ERR_PASSWORD"


@pytest.fixture
def signed_google(monkeypatch: pytest.MonkeyPatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "isolated-google-test")])
    instant = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(instant - timedelta(minutes=1))
        .not_valid_after(instant + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    public = cert.public_bytes(serialization.Encoding.PEM).decode()

    def certificates(*args, **kwargs):
        return SimpleNamespace(
            status=200, data=json.dumps({"test-key": public}).encode(), headers={}
        )

    monkeypatch.setattr(google.GoogleRequest, "__call__", certificates)

    default_subject = "isolated-subject-" + uuid4().hex

    def sign(email: str, nonce: str, subject_id: str | None = None, **overrides):
        payload = {
            "iss": "https://accounts.google.com",
            "aud": AUDIENCE,
            "sub": subject_id or default_subject,
            "email": email,
            "email_verified": True,
            "nonce": nonce,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            **overrides,
        }
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-key"})

    return sign


def test_google_sdk_rejects_wrong_audience_expiry_and_unsigned_tokens(signed_google) -> None:
    assert google.verified_claims(signed_google("synthetic@gmail.com", "n"), AUDIENCE)[
        "sub"
    ].startswith("isolated-subject-")
    for token in [
        signed_google("synthetic@gmail.com", "n", aud="wrong"),
        signed_google("synthetic@gmail.com", "n", exp=int(time.time()) - 60),
        signed_google("synthetic@gmail.com", "n", iss="https://evil.invalid"),
        signed_google("synthetic@gmail.com", "n", email_verified=False),
        jwt.encode({"sub": "forged"}, key="", algorithm="none"),
    ]:
        with pytest.raises(DomainError):
            google.verified_claims(token, AUDIENCE)


@pytest.fixture
async def auth_client(database_settings: FileSettings):
    settings = database_settings.model_copy(update={"google_client_id": AUDIENCE})
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.170", 12345)),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": ORIGIN},
        ) as api,
    ):
        api.headers["X-CSRF-Token"] = (await api.get("/api/v1/auth/csrf")).json()["csrf_token"]
        yield api


@REAL
async def test_resend_replaces_codes_and_three_issuances_pause_new_requests(
    auth_client: httpx.AsyncClient,
) -> None:
    api = auth_client
    email = uuid4().hex + "@example.com"
    first = "Initial account phrase 7! " + uuid4().hex
    second = "Updated account phrase 7! " + uuid4().hex
    assert (
        await api.post("/api/v1/auth/register", json={"email": email, "password": first})
    ).status_code == 202
    code1 = await mail_token(email)
    assert (
        await api.post("/api/v1/auth/register", json={"email": email, "password": second})
    ).status_code == 202
    code2 = await mail_token(email)
    assert code1 != code2
    assert (await api.post("/api/v1/auth/verify", json={"token": code1})).status_code == 410
    assert (
        await api.post("/api/v1/auth/verification-request", json={"email": email})
    ).status_code == 202
    code3 = await mail_token(email)
    assert (await api.post("/api/v1/auth/verify", json={"token": code2})).status_code == 410
    denied = await api.post("/api/v1/auth/verification-request", json={"email": email})
    assert denied.status_code == 429 and denied.json()["error"]["code"] == "ERR_VERIFICATION_LIMIT"
    assert 86000 <= int(denied.headers["Retry-After"]) <= 86400
    assert (await api.post("/api/v1/auth/verify", json={"token": code3})).status_code == 200
    authenticated = await api.post("/api/v1/auth/login", json={"email": email, "password": second})
    assert authenticated.status_code == 200, authenticated.text
    assert (await api.get("/api/v1/auth/me")).json()["verified"]
    assert code3 not in authenticated.text and "refresh_token" not in authenticated.text


@REAL
async def test_google_nonce_replay_email_collision_linking_and_mfa(
    auth_client: httpx.AsyncClient, signed_google
) -> None:
    api = auth_client
    email, password, recovery = await new_identity(api)

    async def proof():
        nonce = (await api.post("/api/v1/auth/google/challenge")).json()["nonce"]
        return {"nonce": nonce, "credential": signed_google(email, nonce)}

    body = await proof()
    collision = await api.post("/api/v1/auth/google/login", json=body)
    assert (
        collision.status_code == 409
        and collision.json()["error"]["code"] == "ERR_GOOGLE_LINK_REQUIRED"
    )
    linked = await api.post("/api/v1/auth/google/link", json=body)
    assert linked.status_code == 200, linked.text
    body = await proof()
    required = await api.post("/api/v1/auth/google/login", json=body)
    assert required.status_code == 401 and required.json()["error"]["code"] == "ERR_MFA_REQUIRED"
    signed_in = await api.post(
        "/api/v1/auth/google/login", json={**body, "recovery_code": recovery[0]}
    )
    assert signed_in.status_code == 200, signed_in.text
    api.headers["X-CSRF-Token"] = (await api.get("/api/v1/auth/csrf")).json()["csrf_token"]
    assert (await api.get("/api/v1/auth/me")).json()["mfa"] is True
    replay = await api.post("/api/v1/auth/google/login", json=body)
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "ERR_GOOGLE_AUTH"
    assert (await api.get("/api/v1/auth/me")).status_code == 200


@REAL
async def test_google_requires_nonce_binding_and_independent_email_proof(
    auth_client: httpx.AsyncClient, signed_google
) -> None:
    api = auth_client
    nonce = (await api.post("/api/v1/auth/google/challenge")).json()["nonce"]
    email = uuid4().hex + "@example.com"
    bad = await api.post(
        "/api/v1/auth/google/login",
        json={"nonce": nonce, "credential": signed_google(email, "wrong-nonce")},
    )
    assert bad.status_code == 401
    unicode_nonce = await api.post(
        "/api/v1/auth/google/login",
        json={"nonce": nonce, "credential": signed_google(email, "α" * 32)},
    )
    assert unicode_nonce.status_code == 401
    valid = await api.post(
        "/api/v1/auth/google/login",
        json={"nonce": nonce, "credential": signed_google(email, nonce)},
    )
    assert (
        valid.status_code == 409
        and valid.json()["error"]["code"] == "ERR_GOOGLE_EMAIL_VERIFICATION_REQUIRED"
    )
    assert api.cookies.get("__Host-lp_access") is None


@REAL
async def test_google_gmail_bootstraps_owned_session_without_bypassing_mfa(
    auth_client: httpx.AsyncClient, signed_google
) -> None:
    api = auth_client
    nonce = (await api.post("/api/v1/auth/google/challenge")).json()["nonce"]
    response = await api.post(
        "/api/v1/auth/google/login",
        json={"nonce": nonce, "credential": signed_google(uuid4().hex + "@gmail.com", nonce)},
    )
    assert response.status_code == 200, response.text
    api.headers["X-CSRF-Token"] = (await api.get("/api/v1/auth/csrf")).json()["csrf_token"]
    identity = (await api.get("/api/v1/auth/me")).json()
    assert identity["verified"] is True and identity["mfa"] is False
    for name in ["__Host-lp_access", "__Host-lp_refresh", "__Host-lp_csrf"]:
        c = next(c for c in api.cookies.jar if c.name == name)
        assert c.secure and c.has_nonstandard_attr("HttpOnly")
    assert (
        await api.post(
            "/api/v1/auth/google/link",
            json={"nonce": nonce, "credential": signed_google(identity["email"], nonce)},
        )
    ).status_code == 403


@REAL
async def test_parallel_registration_cannot_bypass_daily_limit_and_expiry_restores_requests(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from ledgerpilot.identity import service

    api = auth_client
    email = uuid4().hex + "@example.com"
    body = {"email": email, "password": "Parallel request phrase 7! " + uuid4().hex}
    responses = await asyncio.gather(
        *(api.post("/api/v1/auth/register", json=body) for _ in range(3))
    )
    assert all(r.status_code == 202 for r in responses), [r.status_code for r in responses]
    assert (
        await api.post("/api/v1/auth/verification-request", json={"email": email})
    ).status_code == 429
    future = service.now() + timedelta(hours=25)
    monkeypatch.setattr(service, "now", lambda: future)
    assert (
        await api.post("/api/v1/auth/verification-request", json={"email": email})
    ).status_code == 202


@REAL
async def test_google_challenge_is_bound_to_the_requesting_browser_csrf(
    auth_client: httpx.AsyncClient, database_settings: FileSettings, signed_google
) -> None:
    nonce = (await auth_client.post("/api/v1/auth/google/challenge")).json()["nonce"]
    settings = database_settings.model_copy(update={"google_client_id": AUDIENCE})
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.171", 12345)),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": ORIGIN},
        ) as other,
    ):
        other.headers["X-CSRF-Token"] = (await other.get("/api/v1/auth/csrf")).json()["csrf_token"]
        denied = await other.post(
            "/api/v1/auth/google/login",
            json={"nonce": nonce, "credential": signed_google("synthetic@gmail.com", nonce)},
        )
        assert denied.status_code == 401 and denied.json()["error"]["code"] == "ERR_GOOGLE_AUTH"
        assert other.cookies.get("__Host-lp_access") is None


@REAL
async def test_verification_cannot_activate_another_browsers_pending_password(
    auth_client: httpx.AsyncClient, database_settings: FileSettings
) -> None:
    api = auth_client
    email = uuid4().hex + "@example.com"
    assert (
        await api.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "Browser bound phrase 7! " + uuid4().hex},
        )
    ).status_code == 202
    token = await mail_token(email)
    app = create_app(database_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.172", 12345)),
            base_url="https://api.ledgerpilot.test",
            headers={"Origin": ORIGIN},
        ) as other,
    ):
        other.headers["X-CSRF-Token"] = (await other.get("/api/v1/auth/csrf")).json()["csrf_token"]
        failed = await other.post("/api/v1/auth/verify", json={"token": token})
        assert (
            failed.status_code == 410
            and failed.json()["error"]["code"] == "ERR_VERIFICATION_SESSION"
        )
        assert (
            await other.post("/api/v1/auth/verification-request", json={"email": email})
        ).status_code == 202
        assert await mail_token(email) == token
    assert (await api.post("/api/v1/auth/verify", json={"token": token})).status_code == 200


@pytest.mark.parametrize("code", ["１２３４５６", "١٢٣٤٥٦", "abcdef"])
def test_non_ascii_or_invalid_mfa_codes_fail_safely(code: str) -> None:
    from ledgerpilot.identity.security import totp_counter

    with pytest.raises(DomainError) as error:
        totp_counter("JBSWY3DPEHPK3PXP", code, -1)
    assert error.value.code == "ERR_MFA_CODE"


@pytest.mark.parametrize("claim_nonce", ["wrong-nonce", "α" * 32, None])
def test_signed_google_nonce_mismatch_is_rejected_without_type_errors(
    signed_google, claim_nonce
) -> None:
    from typing import cast
    from unittest.mock import MagicMock

    from sqlalchemy.orm import Session

    from ledgerpilot.config import Settings
    from ledgerpilot.identity.models import GoogleChallenge
    from ledgerpilot.identity.schemas import GoogleCredentials
    from ledgerpilot.identity.security import digest

    nonce, csrf = "n" * 43, "c" * 43
    db = MagicMock(spec=Session)
    db.scalar.return_value = GoogleChallenge(
        nonce_hash=digest(nonce),
        csrf_hash=digest(csrf),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        used_at=None,
    )
    settings = cast(Settings, SimpleNamespace(google_client_id=AUDIENCE))
    data = GoogleCredentials(
        credential=signed_google("synthetic@gmail.com", claim_nonce), nonce=nonce
    )
    with pytest.raises(DomainError) as failure:
        google.consume_proof(db, settings, data, csrf)
    assert failure.value.code == "ERR_GOOGLE_AUTH"
