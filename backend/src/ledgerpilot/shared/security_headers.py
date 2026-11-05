"""Response protection also covers CORS preflight and middleware rejections."""

from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ledgerpilot.config import Settings
from ledgerpilot.shared.telemetry import request_logging


def security_headers(settings: Settings, scheme: str, path: str) -> dict[str, str]:
    hosted = settings.app_env not in {"local", "test"}
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Content-Security-Policy": (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        ),
    }
    if not hosted and path == "/docs":
        # Local Swagger loads its existing CDN assets and inline bootstrap.
        # Hosted profiles have no docs endpoint and retain the restrictive policy.
        headers["Content-Security-Policy"] = (
            "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        )
    if hosted and scheme == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with request_logging(scope.setdefault("state", {})):
            await self.http(scope, receive, send)

    async def http(self, scope: Scope, receive: Receive, send: Send) -> None:

        async def protected(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in security_headers(
                    scope["app"].state.settings, scope.get("scheme", ""), scope["path"]
                ).items():
                    headers[name] = value
            await send(message)

        settings = scope["app"].state.settings
        health_probe = scope["method"] in {"GET", "HEAD"} and scope["path"] in {
            "/health/live",
            "/health/ready",
        }
        if (
            settings.app_env not in {"local", "test"}
            and scope.get("scheme") != "https"
            and not health_probe
        ):
            # Outside CORS as well: even preflight must use the hosted TLS endpoint.
            request_id = str(uuid4())
            scope.setdefault("state", {})["request_id"] = request_id
            response = JSONResponse(
                {
                    "error": {
                        "code": "ERR_HTTPS_REQUIRED",
                        "message": "Use the secure HTTPS address to continue.",
                    },
                    "request_id": request_id,
                },
                status_code=403,
                headers={"X-Request-ID": request_id},
            )
            await response(scope, receive, protected)
            return
        await self.app(scope, receive, protected)
