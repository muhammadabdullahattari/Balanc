"""Browser authentication endpoints. Secure cookies are mandatory in every profile."""

from fastapi import APIRouter, Request, Response

from ledgerpilot.audit.service import record
from ledgerpilot.identity import service
from ledgerpilot.identity.dependencies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    DB,
    REFRESH_COOKIE,
    Identity,
)
from ledgerpilot.identity.schemas import (
    Credentials,
    EmailRequest,
    MFARequest,
    Registration,
    ResetRequest,
    TokenRequest,
)
from ledgerpilot.identity.security import now, opaque
from ledgerpilot.shared import contracts

router = APIRouter(prefix="/auth", tags=["identity"])
GENERIC = {"message": "If the request is eligible, an email has been sent."}


def cookies(request: Request, response: Response, values: tuple[str, str, str]) -> None:
    for name, value, age in zip(
        (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE), values, (600, 604800, 604800), strict=True
    ):
        response.set_cookie(
            name,
            value,
            max_age=age,
            secure=True,
            httponly=True,
            samesite=request.app.state.settings.cookie_samesite,
            path="/",
        )
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/csrf", response_model=contracts.responses["identity.csrf"], response_model_exclude_unset=True
)
def csrf(request: Request, response: Response) -> dict[str, str]:
    token = request.cookies.get(CSRF_COOKIE) or opaque()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=604800,
        secure=True,
        httponly=True,
        samesite=request.app.state.settings.cookie_samesite,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token}


@router.post(
    "/register",
    status_code=202,
    response_model=contracts.responses["identity.register"],
    response_model_exclude_unset=True,
)
def register(data: Registration, request: Request, db: DB) -> dict[str, str]:
    service.register(db, request.app.state.settings, data)
    return GENERIC


@router.post(
    "/verify",
    response_model=contracts.responses["identity.verify"],
    response_model_exclude_unset=True,
)
def verify(data: TokenRequest, db: DB) -> dict[str, str]:
    service.verify(db, data.token.get_secret_value())
    return {"status": "verified"}


@router.post(
    "/verification-request",
    status_code=202,
    response_model=contracts.responses["identity.verification_request"],
    response_model_exclude_unset=True,
)
def verification_request(data: EmailRequest, request: Request, db: DB) -> dict[str, str]:
    service.request_action(db, request.app.state.settings, data.email, "verify")
    return GENERIC


@router.post(
    "/reset-request",
    status_code=202,
    response_model=contracts.responses["identity.reset_request"],
    response_model_exclude_unset=True,
)
def reset_request(data: EmailRequest, request: Request, db: DB) -> dict[str, str]:
    service.request_action(db, request.app.state.settings, data.email, "reset")
    return GENERIC


@router.post(
    "/reset",
    response_model=contracts.responses["identity.reset"],
    response_model_exclude_unset=True,
)
def reset(data: ResetRequest, db: DB) -> dict[str, str]:
    service.reset_password(db, data.token.get_secret_value(), data.new_password.get_secret_value())
    return {"status": "reset"}


@router.post(
    "/login",
    response_model=contracts.responses["identity.login"],
    response_model_exclude_unset=True,
)
def login(data: Credentials, request: Request, response: Response, db: DB) -> dict[str, str]:
    cookies(request, response, service.login(db, request.app.state.settings, data))
    return {"status": "authenticated"}


@router.post(
    "/refresh",
    response_model=contracts.responses["identity.refresh"],
    response_model_exclude_unset=True,
)
def refresh(request: Request, response: Response, db: DB) -> dict[str, str]:
    cookies(
        request,
        response,
        service.refresh(
            db,
            request.app.state.settings,
            request.cookies.get(REFRESH_COOKIE, ""),
            request.headers.get("x-csrf-token", ""),
        ),
    )
    return {"status": "refreshed"}


@router.get(
    "/me", response_model=contracts.responses["identity.me"], response_model_exclude_unset=True
)
def me(identity: Identity) -> dict[str, object]:
    return {
        "id": str(identity.user.id),
        "email": identity.user.email,
        "verified": identity.user.verified,
        "mfa": identity.family.mfa,
    }


@router.post(
    "/logout",
    response_model=contracts.responses["identity.logout"],
    response_model_exclude_unset=True,
)
def logout(request: Request, response: Response, identity: Identity, db: DB) -> dict[str, str]:
    identity.family.revoked_at = now()
    record(db, identity.user.id, "session.revoked", resource_id=identity.family.id)
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            secure=True,
            httponly=True,
            samesite=request.app.state.settings.cookie_samesite,
        )
    return {"status": "logged_out"}


@router.post(
    "/logout-all",
    response_model=contracts.responses["identity.logout_all"],
    response_model_exclude_unset=True,
)
def logout_all(identity: Identity, db: DB) -> dict[str, str]:
    service.revoke_all(db, identity.user.id)
    record(db, identity.user.id, "session.all_revoked")
    return {"status": "revoked"}


@router.post(
    "/mfa/enroll",
    response_model=contracts.responses["identity.enroll"],
    response_model_exclude_unset=True,
)
def enroll(request: Request, identity: Identity, db: DB, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return service.enroll_mfa(db, request.app.state.settings, identity.user.id)


@router.post(
    "/mfa/confirm",
    response_model=contracts.responses["identity.confirm"],
    response_model_exclude_unset=True,
)
def confirm(
    data: MFARequest, request: Request, identity: Identity, db: DB, response: Response
) -> dict[str, list[str]]:
    response.headers["Cache-Control"] = "no-store"
    return {
        "recovery_codes": service.confirm_mfa(
            db, request.app.state.settings, identity.family, data.code.get_secret_value()
        )
    }
