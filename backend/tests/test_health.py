"""Liveness route contract tests."""

import httpx
from fastapi.routing import APIRoute

from ledgerpilot.main import app


def test_liveness_route_is_registered_and_reports_process_health() -> None:
    """The liveness route is registered as GET and does not need external services."""
    route = next(
        (
            candidate
            for candidate in app.routes
            if isinstance(candidate, APIRoute) and candidate.path == "/health/live"
        ),
        None,
    )

    assert route is not None
    assert route.methods == {"GET"}
    assert route.endpoint() == {"status": "ok"}


async def test_non_ascii_csrf_header_is_rejected_without_database_access() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as browser:
        response = await browser.post(
            "/api/v1/auth/login",
            headers=[
                (b"origin", b"http://localhost:5173"),
                (b"cookie", b"__Host-lp_csrf=ordinary-ascii-token"),
                (b"x-csrf-token", b"\xff"),
            ],
            json={},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ERR_CSRF"
