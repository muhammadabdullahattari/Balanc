"""Liveness route contract tests."""

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
