"""Application lifecycle, probes and the single API registration point."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException

from ledgerpilot.accounting.router import router as accounting_router
from ledgerpilot.banking.router import router as banking_router
from ledgerpilot.config import Settings, get_settings
from ledgerpilot.database import build_engine
from ledgerpilot.documents.router import router as evidence_router
from ledgerpilot.identity.router import router as identity_router
from ledgerpilot.reports.router import router as reports_router
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.middleware import FoundationMiddleware
from ledgerpilot.shared.responses import ErrorEnvelope, ReadinessResponse
from ledgerpilot.shared.security_headers import SecurityHeadersMiddleware, security_headers
from ledgerpilot.shared.telemetry import emit
from ledgerpilot.swagger import local_swagger
from ledgerpilot.tenancy.router import router as tenancy_router
from ledgerpilot.trade.router import router as trade_router
from ledgerpilot.workspace.router import router as workspace_router


def create_app(settings: Settings | None = None) -> FastAPI:

    configured = settings or get_settings()
    logger = logging.getLogger("ledgerpilot")
    logger.disabled = False
    # Alembic fileConfig can disable existing loggers in an embedded invocation.
    for name in ("ledgerpilot.requests", "ledgerpilot.errors", "ledgerpilot.operations"):
        logging.getLogger(name).disabled = False
    logger.setLevel(configured.log_level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(configured)
        rate_engine = build_engine(configured, rate_limits=True)
        application.state.engine = engine
        application.state.sessions = sessionmaker(engine, expire_on_commit=False)
        application.state.rate_sessions = sessionmaker(rate_engine, expire_on_commit=False)
        try:
            yield
        finally:
            rate_engine.dispose()
            engine.dispose()

    local_docs = configured.app_env in {"local", "test"}
    error_contracts: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorEnvelope}
        for code in (400, 401, 403, 404, 405, 409, 410, 412, 413, 416, 422, 429, 500, 503)
    }
    application = FastAPI(
        title=configured.app_name,
        version=version("ledgerpilot-api"),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if local_docs else None,
        responses=error_contracts,
    )
    application.state.settings = configured
    if local_docs:

        @application.get("/docs", include_in_schema=False)
        def documentation() -> HTMLResponse:
            return local_swagger(
                "/openapi.json",
                f"{configured.app_name} - Swagger UI",
                f"{configured.api_v1_prefix}/auth/csrf",
            )

    application.add_middleware(FoundationMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Range", "If-Match"],
        expose_headers=[
            "X-Request-ID",
            "Retry-After",
            "ETag",
            "Accept-Ranges",
            "Content-Range",
            "Content-Disposition",
        ],
    )
    application.add_middleware(SecurityHeadersMiddleware)

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        emit(
            "request.rejected",
            level=logging.WARNING if error.status >= 400 else logging.INFO,
            code=error.code,
            status=error.status,
            request_id=getattr(request.state, "request_id", None),
            user_id=getattr(request.state, "user_id", None),
            business_id=getattr(request.state, "business_id", None),
        )
        return JSONResponse(
            {
                "error": {"code": error.code, "message": error.message},
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=error.status,
            headers={"Retry-After": str(error.retry_after)} if error.retry_after else None,
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            {
                "error": {"code": "ERR_HTTP", "message": "Request cannot be handled."},
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=error.status_code,
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "ERR_VALIDATION",
                    "message": "Invalid request.",
                    "fields": [
                        {"location": list(e["loc"]), "type": e["type"]} for e in error.errors()
                    ],
                },
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=422,
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        state = getattr(getattr(error, "orig", None), "sqlstate", "") or ""
        code = "ERR_CONFLICT" if state.startswith(("23", "40")) else "ERR_DEPENDENCY"
        message = "Operation could not complete."
        if getattr(
            getattr(getattr(error, "orig", None), "diag", None), "message_primary", None
        ) == ("ERR_PREPAID_PENDING"):
            code = "ERR_PREPAID_PENDING"
            message = "Approve due prepaid installments before locking this period."
        status = 503 if code == "ERR_DEPENDENCY" else 409
        logging.getLogger("ledgerpilot.errors").warning(
            json.dumps(
                {
                    "event": "database_failure",
                    "code": code,
                    "sqlstate": state,
                    "request_id": getattr(request.state, "request_id", None),
                }
            )
        )
        return JSONResponse(
            {
                "error": {"code": code, "message": message},
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=status,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logging.getLogger("ledgerpilot.errors").error(
            json.dumps(
                {
                    "event": "unexpected_error",
                    "type": type(error).__name__,
                    "request_id": getattr(request.state, "request_id", None),
                }
            )
        )
        return JSONResponse(
            {
                "error": {"code": "ERR_INTERNAL", "message": "Operation could not complete."},
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=500,
            # Starlette's outer ServerErrorMiddleware creates this response
            # outside user middleware; attach the same protections explicitly.
            headers=security_headers(configured, request.url.scheme, request.url.path),
        )

    @application.get("/health/live", tags=["health"], response_model=ReadinessResponse)
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get(
        "/health/ready",
        tags=["health"],
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as connection:
                safe = connection.execute(
                    text("""
                    SELECT current_user=:role AND NOT (rolsuper OR rolcreatedb OR rolcreaterole
                        OR rolreplication OR rolbypassrls)
                    FROM pg_roles WHERE rolname=current_user
                """),
                    {"role": configured.app_db_user},
                ).scalar_one()
                version = connection.execute(
                    text("SELECT version_num FROM app.alembic_version")
                ).scalar_one()
                if not safe or version != "0026":
                    raise ValueError
            return JSONResponse({"status": "ready"})
        except (SQLAlchemyError, ValueError):
            return JSONResponse({"status": "not_ready"}, status_code=503)

    application.include_router(identity_router, prefix=configured.api_v1_prefix)
    application.include_router(tenancy_router, prefix=configured.api_v1_prefix)
    application.include_router(accounting_router, prefix=configured.api_v1_prefix)
    application.include_router(trade_router, prefix=configured.api_v1_prefix)
    application.include_router(banking_router, prefix=configured.api_v1_prefix)
    application.include_router(reports_router, prefix=configured.api_v1_prefix)
    application.include_router(workspace_router, prefix=configured.api_v1_prefix)
    application.include_router(evidence_router, prefix=configured.api_v1_prefix)
    return application


app = create_app()
