"""Prometheus metrics endpoint exposition router."""

from fastapi import APIRouter, Request, Response

from ledgerpilot.observability.metrics import format_prometheus_metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", response_class=Response)
def metrics(request: Request) -> Response:
    engine = getattr(request.app.state, "engine", None)
    content = format_prometheus_metrics(engine)
    return Response(content, media_type="text/plain; version=0.0.4; charset=utf-8")
