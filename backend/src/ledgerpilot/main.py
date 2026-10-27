"""FastAPI application entry point."""

from fastapi import FastAPI

from ledgerpilot.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    """Report that this application process is running."""

    return {"status": "ok"}
