"""Validated local extraction response; no arbitrary tools or model actions."""

import json
import urllib.error
import urllib.request
from typing import Literal

from pydantic import Field

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema


class ExtractedField(Schema):
    name: str = Field(max_length=80)
    value: str = Field(max_length=1000)
    value_type: Literal["text", "date", "amount"]
    page: int = Field(ge=1, le=10)
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    semantic_role: Literal["unassigned"] = "unassigned"


class Extraction(Schema):
    status: Literal["extracted", "unsupported"]
    model_version: str | None = Field(max_length=240)
    extractor_version: str
    language: str
    confidence_kind: str
    raw_text: str | None = Field(max_length=200000)
    fields: list[ExtractedField] = Field(max_length=20000)
    reason: str | None = Field(default=None, max_length=500)
    requires_review: bool = True


def extract(content: bytes, media_type: str, settings: Settings) -> Extraction:
    if not settings.ocr_enabled or settings.ocr_service_token is None:
        raise DomainError("ERR_OCR_UNAVAILABLE", "Local OCR is not enabled.", 503)
    request = urllib.request.Request(
        settings.ocr_service_url,
        data=content,
        headers={
            "Content-Type": media_type,
            "Authorization": "Bearer " + settings.ocr_service_token.get_secret_value(),
        },
    )
    try:
        # Server-configured loopback/private DNS only. Never follow parser-service redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: object, **kwargs: object) -> None:
                return None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=95) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Extraction response too large")
        return Extraction.model_validate(json.loads(raw))
    except (OSError, ValueError, urllib.error.URLError):
        raise DomainError(
            "ERR_OCR_UNAVAILABLE", "The local extractor could not return verified fields.", 503
        ) from None
