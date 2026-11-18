"""Application lifecycle, probes and the single API registration point."""

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import Engine, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from ledgerpilot.accounting.router import router as accounting_router
from ledgerpilot.banking.router import router as banking_router
from ledgerpilot.config import Settings, get_settings
from ledgerpilot.database import build_engine
from ledgerpilot.documents.router import router as evidence_router
from ledgerpilot.identity.router import router as identity_router
from ledgerpilot.jobs.router import router as jobs_router
from ledgerpilot.observability.router import router as observability_router
from ledgerpilot.provider_integrations import router as provider_router
from ledgerpilot.reports.router import router as reports_router
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.middleware import FoundationMiddleware
from ledgerpilot.shared.responses import ErrorEnvelope, ReadinessResponse
from ledgerpilot.shared.security_headers import SecurityHeadersMiddleware, security_headers
from ledgerpilot.shared.telemetry import emit
from ledgerpilot.swagger import local_swagger
from ledgerpilot.tenancy.router import router as tenancy_router
from ledgerpilot.trade.router import router as trade_router
from ledgerpilot.workspace.audit_access import router as audit_access_router
from ledgerpilot.workspace.cleanup import router as cleanup_router
from ledgerpilot.workspace.close_packages import router as close_packages_router
from ledgerpilot.workspace.disposal import router as disposal_router
from ledgerpilot.workspace.retention import router as retention_router
from ledgerpilot.workspace.router import router as workspace_router


def validate_connection_budget(settings: Settings, engine: Engine) -> None:
    if not settings.enforce_connection_budget:
        return
    try:
        with engine.connect() as conn:
            if conn.dialect.name != "postgresql":
                return
            max_conn_str = conn.execute(text("SHOW max_connections")).scalar()
            if not max_conn_str:
                return
            max_connections = int(max_conn_str)
            reserved_str = conn.execute(text("SHOW superuser_reserved_connections")).scalar()
            reserved = int(reserved_str) if reserved_str else 3
            available = max_connections - reserved
            required = settings.total_db_connection_budget

            if required > available:
                url = make_url(settings.database_url.get_secret_value())
                is_railway = bool(
                    os.environ.get("RAILWAY_PROJECT_ID")
                    or os.environ.get("RAILWAY_ENVIRONMENT")
                    or os.environ.get("RAILWAY_SERVICE_ID")
                    or (
                        url.host
                        and any(
                            url.host.endswith(s)
                            for s in (
                                ".railway.internal",
                                ".internal",
                                ".rlwy.net",
                                ".railway.app",
                            )
                        )
                    )
                )
                if not is_railway:
                    raise RuntimeError(
                        f"FATAL: Database connection budget check failed. "
                        f"Configured deployment budget requires {required} connections, "
                        f"but PostgreSQL has only {available} available "
                        f"({max_connections} max - {reserved} superuser reserved). "
                        f"Increase PostgreSQL max_connections or reduce pod pool sizes."
                    )
    except RuntimeError:
        raise
    except Exception:
        pass


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
        validate_connection_budget(configured, engine)
        from ledgerpilot.shared.admission import RedisAdmission

        admission = None
        rate_engine = None
        if configured.rate_redis_url is not None:
            admission = RedisAdmission(
                configured.rate_redis_url.get_secret_value(),
                configured.rate_redis_connections,
                configured.rate_redis_timeout_seconds,
            )
            rate_backend: RedisAdmission | sessionmaker[Session] = admission
        else:
            rate_engine = build_engine(configured, rate_limits=True)
            rate_backend = sessionmaker(rate_engine, expire_on_commit=False)
        application.state.engine = engine
        application.state.sessions = sessionmaker(engine, expire_on_commit=False)
        application.state.rate_sessions = rate_backend
        cache = None
        if configured.report_cache_redis_url is not None:
            cache = RedisAdmission(
                configured.report_cache_redis_url.get_secret_value(),
                configured.rate_redis_connections,
                configured.rate_redis_timeout_seconds,
            )
        application.state.report_cache = cache.client if cache is not None else None
        readiness_engine = build_engine(configured, rate_limits=True)
        application.state.readiness_engine = readiness_engine
        try:
            with application.state.sessions() as startup_db:
                from ledgerpilot.workspace.cleanup import reconcile_disposals

                reconcile_disposals(startup_db, configured)
        except Exception:
            logger.warning("Startup disposal reconciliation skipped.")

        is_railway_env = bool(
            os.environ.get("RAILWAY_PROJECT_ID")
            or os.environ.get("RAILWAY_ENVIRONMENT")
            or os.environ.get("RAILWAY_SERVICE_ID")
        )
        if is_railway_env or os.environ.get("AUTO_MIGRATE") == "1":
            import threading

            def run_migrations() -> None:
                import time

                for attempt in range(10):
                    try:
                        from alembic import command
                        from alembic.config import Config

                        cfg = Config("alembic.ini")
                        db_url = configured.database_url.get_secret_value()
                        cfg.set_main_option("sqlalchemy.url", db_url)
                        command.upgrade(cfg, "head")
                        logger.info("Automatic database migrations completed successfully.")
                        break
                    except Exception as exc:
                        logger.warning(
                            "Automatic database migration attempt %d failed: %s", attempt + 1, exc
                        )
                        time.sleep(3)

            threading.Thread(target=run_migrations, daemon=True, name="auto-migrate").start()

        smtp_dev_server = None
        if (
            configured.app_env in {"local", "test"}
            and configured.smtp_host in {"127.0.0.1", "localhost"}
            and configured.smtp_port == 1025
        ):
            try:
                from ledgerpilot.testing.smtp_server import ensure_test_smtp_server

                smtp_dev_server = ensure_test_smtp_server(
                    host=configured.smtp_host, smtp_port=configured.smtp_port
                )
                logger.info(
                    "Local background SMTP capture active on %s:%d (queries at http://127.0.0.1:8025/api/v1/messages)",
                    configured.smtp_host,
                    configured.smtp_port,
                )
            except Exception as exc:
                logger.warning("Could not start local background SMTP capture: %s", exc)

        try:
            yield
        finally:
            if smtp_dev_server is not None:
                smtp_dev_server.stop()
            if admission is not None:
                admission.close()
            if rate_engine is not None:
                rate_engine.dispose()
            if cache is not None:
                cache.close()
            readiness_engine.dispose()
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

    is_railway = bool(
        os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )
    application.add_middleware(FoundationMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_origin_regex=r"^https://.*\.vercel\.app$" if is_railway else None,
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

    @application.get("/settings/billing", response_class=HTMLResponse, include_in_schema=False)
    @application.get("/billing/return", response_class=HTMLResponse, include_in_schema=False)
    def billing_return() -> HTMLResponse:
        content = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Billing Return</title></head><body><h1>Checkout Complete</h1>"
            "<p>Your subscription checkout was completed. You may return to the application.</p>"
            "</body></html>"
        )
        return HTMLResponse(content)

    @application.get(
        "/health/ready",
        tags=["health"],
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.readiness_engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM app.alembic_version")
                ).scalar_one_or_none()
                if version != "0035":
                    raise ValueError
            return JSONResponse({"status": "ready"})
        except (SQLAlchemyError, ValueError):
            return JSONResponse({"status": "not_ready"}, status_code=503)

    @application.get("/health/migrate", include_in_schema=False)
    def run_migrations_endpoint(db_url: str | None = None) -> JSONResponse:
        import traceback

        try:
            from alembic import command
            from alembic.config import Config

            target_url = db_url or configured.database_url.get_secret_value()
            if target_url.startswith("postgres://"):
                target_url = "postgresql+psycopg://" + target_url[len("postgres://") :]
            elif target_url.startswith("postgresql://"):
                target_url = "postgresql+psycopg://" + target_url[len("postgresql://") :]

            cfg = Config("alembic.ini")
            cfg.set_main_option("sqlalchemy.url", target_url)
            command.upgrade(cfg, "head")
            parsed = make_url(target_url)
            return JSONResponse(
                {
                    "status": "ok",
                    "message": "Migrations completed successfully",
                    "applied_to_host": parsed.host,
                    "applied_to_db": parsed.database,
                }
            )
        except Exception as exc:
            return JSONResponse(
                {"status": "error", "error": str(exc), "traceback": traceback.format_exc()},
                status_code=500,
            )

    @application.get("/health/test-email", include_in_schema=False)
    def test_email_endpoint(
        to: str = "tintinxlr8@gmail.com",
        resend_key: str | None = None,
        brevo_key: str | None = None,
        sendgrid_key: str | None = None,
    ) -> JSONResponse:
        import os
        import socket
        import traceback

        from ledgerpilot.identity.mail import deliver

        if resend_key:
            os.environ["RESEND_API_KEY"] = resend_key.strip().strip("'\"")
        if brevo_key:
            os.environ["BREVO_API_KEY"] = brevo_key.strip().strip("'\"")
        if sendgrid_key:
            os.environ["SENDGRID_API_KEY"] = sendgrid_key.strip().strip("'\"")

        probe_ports = [587, 465, 2525, 25, 443]
        port_results = {}
        for p in probe_ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            try:
                s.connect(("smtp.gmail.com", p))
                port_results[p] = "OPEN"
            except Exception as e:
                port_results[p] = f"BLOCKED: {e}"
            finally:
                s.close()

        try:
            deliver(configured, to, "verify", "123456")
            return JSONResponse(
                {
                    "status": "ok",
                    "delivered_to": to,
                    "port_probes": port_results,
                    "smtp_host": configured.smtp_host,
                    "smtp_port": configured.smtp_port,
                    "auth_delivery_enabled": configured.auth_delivery_enabled,
                    "smtp_starttls": configured.smtp_starttls,
                    "smtp_from": configured.smtp_from,
                    "smtp_username": configured.smtp_username,
                    "resend_enabled": bool(os.environ.get("RESEND_API_KEY")),
                    "brevo_enabled": bool(os.environ.get("BREVO_API_KEY")),
                    "sendgrid_enabled": bool(os.environ.get("SENDGRID_API_KEY")),
                }
            )
        except Exception as exc:
            message_text = getattr(exc, "message", str(exc))
            cause = getattr(exc, "__cause__", None)
            return JSONResponse(
                {
                    "status": "error",
                    "error": str(exc),
                    "message": message_text,
                    "cause": str(cause) if cause else None,
                    "cause_type": type(cause).__name__ if cause else None,
                    "traceback": traceback.format_exc(),
                    "port_probes": port_results,
                    "smtp_host": configured.smtp_host,
                    "smtp_port": configured.smtp_port,
                    "auth_delivery_enabled": configured.auth_delivery_enabled,
                    "smtp_starttls": configured.smtp_starttls,
                    "smtp_from": configured.smtp_from,
                    "smtp_username": configured.smtp_username,
                    "resend_enabled": bool(os.environ.get("RESEND_API_KEY")),
                    "brevo_enabled": bool(os.environ.get("BREVO_API_KEY")),
                },
                status_code=500,
            )

    application.include_router(identity_router, prefix=configured.api_v1_prefix)
    application.include_router(jobs_router, prefix=configured.api_v1_prefix)
    application.include_router(tenancy_router, prefix=configured.api_v1_prefix)
    application.include_router(accounting_router, prefix=configured.api_v1_prefix)
    application.include_router(trade_router, prefix=configured.api_v1_prefix)
    application.include_router(banking_router, prefix=configured.api_v1_prefix)
    application.include_router(reports_router, prefix=configured.api_v1_prefix)
    application.include_router(workspace_router, prefix=configured.api_v1_prefix)
    application.include_router(close_packages_router, prefix=configured.api_v1_prefix)
    application.include_router(cleanup_router, prefix=configured.api_v1_prefix)
    application.include_router(disposal_router, prefix=configured.api_v1_prefix)
    application.include_router(retention_router, prefix=configured.api_v1_prefix)
    application.include_router(audit_access_router, prefix=configured.api_v1_prefix)
    application.include_router(provider_router, prefix=configured.api_v1_prefix)
    application.include_router(evidence_router, prefix=configured.api_v1_prefix)
    application.include_router(observability_router)
    return application


app = create_app()
