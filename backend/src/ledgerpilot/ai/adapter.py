"""Provider-agnostic AI model adapter with Groq LLM/Whisper and deterministic fallback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from ledgerpilot.config import Settings

logger = logging.getLogger("ledgerpilot.operations")

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a|an|in)\b", re.IGNORECASE),
    re.compile(
        r"(?:system\s*prompt|###\s*system|<\s*\|\s*im_start\s*\|\s*>|<\s*\|\s*system\s*\|\s*>)",
        re.IGNORECASE,
    ),
    re.compile(r"\[\s*INST\s*\]|\[\s*/\s*INST\s*\]", re.IGNORECASE),
    re.compile(
        r"(?:drop\s+table|truncate\s+table|grant\s+all\s+privileges|bypassrls)", re.IGNORECASE
    ),
    re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL),
)


def sanitize_untrusted_text(raw: str | None, *, max_length: int = 1000) -> tuple[str, bool]:
    """Strip control chars and prompt-injection directives from untrusted data/documents."""
    if not raw:
        return "", False
    cleaned = "".join(ch for ch in str(raw) if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    sanitized = False
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("[REDACTED_UNTRUSTED_DIRECTIVE]", cleaned)
            sanitized = True
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned.strip(), sanitized


class AIModelError(Exception):
    """Typed model adapter error that never leaks secrets or provider internals."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class ModelGenerationResult:
    provider: str
    model_name: str
    content: str
    structured: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    estimated_cost_usd: Decimal
    fallback_used: bool
    fallback_reason: str = ""


@dataclass(frozen=True)
class TranscriptionResult:
    provider: str
    model_name: str
    transcript: str
    language_detected: str
    duration_seconds: int
    latency_ms: int
    estimated_cost_usd: Decimal
    fallback_used: bool
    fallback_reason: str = ""


class ModelAdapter(Protocol):
    """Provider-agnostic interface for structured accounting LLM calls and multilingual STT."""

    def generate_structured(
        self,
        *,
        feature_id: str,
        system_prompt: str,
        user_prompt: str,
        fallback_payload: dict[str, Any],
    ) -> ModelGenerationResult: ...

    def transcribe_audio(
        self,
        *,
        audio_base64: str,
        audio_format: str,
        language_locale: str,
        fallback_transcript: str,
    ) -> TranscriptionResult: ...


def _estimate_token_cost_usd(prompt_tokens: int, completion_tokens: int) -> Decimal:
    # Reference Groq Llama-3.3-70B pricing ($0.59 / 1M input, $0.79 / 1M output)
    in_cost = (Decimal(prompt_tokens) * Decimal("0.59")) / Decimal("1000000")
    out_cost = (Decimal(completion_tokens) * Decimal("0.79")) / Decimal("1000000")
    return (in_cost + out_cost).quantize(Decimal("0.000001"))


class DeterministicModelAdapter:
    """Offline fallback deterministic adapter formatting verified tool outputs."""

    def __init__(self, model_name: str = "deterministic-accounting-engine-v1") -> None:
        self.model_name = model_name

    def generate_structured(
        self,
        *,
        feature_id: str,
        system_prompt: str,
        user_prompt: str,
        fallback_payload: dict[str, Any],
    ) -> ModelGenerationResult:
        content = json.dumps(fallback_payload, sort_keys=True)
        p_tokens = max(12, len(system_prompt.split()) + len(user_prompt.split()))
        c_tokens = max(8, len(content.split()))
        return ModelGenerationResult(
            provider="deterministic_fallback",
            model_name=self.model_name,
            content=content,
            structured=dict(fallback_payload),
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=p_tokens + c_tokens,
            latency_ms=2,
            estimated_cost_usd=Decimal("0.000000"),
            fallback_used=True,
            fallback_reason="deterministic_mode",
        )

    def transcribe_audio(
        self,
        *,
        audio_base64: str,
        audio_format: str,
        language_locale: str,
        fallback_transcript: str,
    ) -> TranscriptionResult:
        cleaned, _ = sanitize_untrusted_text(fallback_transcript, max_length=600)
        return TranscriptionResult(
            provider="deterministic_fallback",
            model_name="deterministic-stt-v1",
            transcript=cleaned or "Record supplier invoice for 450.00 USD.",
            language_detected=language_locale,
            duration_seconds=max(1, min(300, len(audio_base64) // 1600 or 6)),
            latency_ms=2,
            estimated_cost_usd=Decimal("0.000000"),
            fallback_used=True,
            fallback_reason="deterministic_mode",
        )


HttpSender = Callable[[urllib.request.Request, float], tuple[int, dict[str, str], bytes]]


def _default_http_sender(
    req: urllib.request.Request, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return int(resp.status), headers, resp.read()


class GroqModelAdapter:
    """Groq Cloud API adapter with timeouts, retry/backoff, usage accounting, and fallback."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_sender: HttpSender | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        force_live_in_test: bool = False,
    ) -> None:
        self.settings = settings
        self._http_sender = http_sender or _default_http_sender
        self._sleep = sleep_fn
        self._force_live_in_test = force_live_in_test
        self._fallback = DeterministicModelAdapter()

    def _resolve_api_key(self) -> str:
        secret = self.settings.groq_api_key
        raw = secret.get_secret_value().strip().strip("'\"") if secret else ""
        if not raw:
            raw = os.environ.get("GROQ_API_KEY", "").strip().strip("'\"")
        return raw

    def is_configured(self) -> bool:
        return bool(self._resolve_api_key())

    def generate_structured(
        self,
        *,
        feature_id: str,
        system_prompt: str,
        user_prompt: str,
        fallback_payload: dict[str, Any],
    ) -> ModelGenerationResult:
        api_key = self._resolve_api_key()
        if not api_key or (self.settings.app_env == "test" and not self._force_live_in_test):
            return self._fallback.generate_structured(
                feature_id=feature_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_payload=fallback_payload,
            )

        started = time.monotonic()
        base_url = self.settings.groq_base_url.rstrip("/")
        url = f"{base_url}/chat/completions"
        model_name = self.settings.groq_llm_model
        body = {
            "model": model_name,
            "temperature": 0.1,
            "max_tokens": self.settings.ai_max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        encoded = json.dumps(body).encode("utf-8")
        max_retries = max(0, int(self.settings.ai_max_retries))
        timeout = float(self.settings.ai_request_timeout_seconds)
        last_error_code = "ERR_AI_PROVIDER"

        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url,
                data=encoded,
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "LedgerPilot-AI/1.0",
                },
            )
            try:
                status, _headers, raw_bytes = self._http_sender(req, timeout)
                if status == 429 or status >= 500:
                    last_error_code = "ERR_AI_RATE_LIMIT" if status == 429 else "ERR_AI_UPSTREAM"
                    if attempt < max_retries:
                        self._sleep(min(2.0, 0.25 * (2**attempt)))
                        continue
                    break
                payload = json.loads(raw_bytes.decode("utf-8"))
                choices = payload.get("choices") or []
                message = (choices[0].get("message") if choices else {}) or {}
                raw_content = str(message.get("content") or "{}")
                parsed = json.loads(raw_content)
                if not isinstance(parsed, dict):
                    parsed = dict(fallback_payload)
                merged = {**fallback_payload, **parsed}
                usage = payload.get("usage") or {}
                p_tok = int(usage.get("prompt_tokens") or max(10, len(user_prompt.split())))
                c_tok = int(usage.get("completion_tokens") or max(10, len(raw_content.split())))
                t_tok = int(usage.get("total_tokens") or (p_tok + c_tok))
                elapsed_ms = max(1, int((time.monotonic() - started) * 1000))
                return ModelGenerationResult(
                    provider="groq",
                    model_name=str(payload.get("model") or model_name),
                    content=json.dumps(merged, sort_keys=True),
                    structured=merged,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    total_tokens=t_tok,
                    latency_ms=elapsed_ms,
                    estimated_cost_usd=_estimate_token_cost_usd(p_tok, c_tok),
                    fallback_used=False,
                )
            except urllib.error.HTTPError as exc:
                status = int(getattr(exc, "code", 502) or 502)
                last_error_code = "ERR_AI_RATE_LIMIT" if status == 429 else f"ERR_AI_HTTP_{status}"
                if (status == 429 or status >= 500) and attempt < max_retries:
                    retry_hdr = exc.headers.get("Retry-After") if exc.headers else None
                    delay = (
                        min(2.0, float(retry_hdr))
                        if retry_hdr and retry_hdr.isdigit()
                        else min(2.0, 0.25 * (2**attempt))
                    )
                    self._sleep(delay)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
                last_error_code = "ERR_AI_TIMEOUT_OR_PARSE"
                if attempt < max_retries:
                    self._sleep(min(1.5, 0.2 * (2**attempt)))
                    continue
                break

        logger.warning(
            json.dumps(
                {
                    "event": "ai.model_fallback",
                    "feature_id": feature_id,
                    "reason": last_error_code,
                }
            )
        )
        fallback_res = self._fallback.generate_structured(
            feature_id=feature_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_payload=fallback_payload,
        )
        return ModelGenerationResult(
            provider=fallback_res.provider,
            model_name=fallback_res.model_name,
            content=fallback_res.content,
            structured=fallback_res.structured,
            prompt_tokens=fallback_res.prompt_tokens,
            completion_tokens=fallback_res.completion_tokens,
            total_tokens=fallback_res.total_tokens,
            latency_ms=max(1, int((time.monotonic() - started) * 1000)),
            estimated_cost_usd=Decimal("0.000000"),
            fallback_used=True,
            fallback_reason=last_error_code,
        )

    def transcribe_audio(
        self,
        *,
        audio_base64: str,
        audio_format: str,
        language_locale: str,
        fallback_transcript: str,
    ) -> TranscriptionResult:
        api_key = self._resolve_api_key()
        if (
            not api_key
            or not audio_base64.strip()
            or (self.settings.app_env == "test" and not self._force_live_in_test)
        ):
            return self._fallback.transcribe_audio(
                audio_base64=audio_base64,
                audio_format=audio_format,
                language_locale=language_locale,
                fallback_transcript=fallback_transcript,
            )

        started = time.monotonic()
        try:
            raw_audio = base64.b64decode(audio_base64, validate=False)
        except Exception:
            raw_audio = b""

        if not raw_audio or len(raw_audio) < 16:
            return self._fallback.transcribe_audio(
                audio_base64=audio_base64,
                audio_format=audio_format,
                language_locale=language_locale,
                fallback_transcript=fallback_transcript,
            )

        boundary = "----LedgerPilotGroqAudioBoundary7f9a2b"
        ext = "wav" if "wav" in audio_format.lower() else "webm"
        iso_lang = "ur" if language_locale.startswith("ur") else "en"
        stt_model = self.settings.groq_stt_model
        parts: list[bytes] = []

        def _add_field(name: str, val: str) -> None:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())

        _add_field("model", stt_model)
        if language_locale != "en_ur_mixed":
            _add_field("language", iso_lang)
        _add_field("response_format", "json")
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="file"; filename="voice.{ext}"\r\n'
            f"Content-Type: {audio_format or 'audio/webm'}\r\n\r\n".encode()
        )
        parts.append(raw_audio)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body_bytes = b"".join(parts)

        base_url = self.settings.groq_base_url.rstrip("/")
        url = f"{base_url}/audio/transcriptions"
        req = urllib.request.Request(
            url,
            data=body_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": "LedgerPilot-AI/1.0",
            },
        )
        try:
            status, _headers, resp_bytes = self._http_sender(
                req, float(self.settings.ai_request_timeout_seconds)
            )
            if 200 <= status < 300:
                data = json.loads(resp_bytes.decode("utf-8"))
                text_out, _ = sanitize_untrusted_text(str(data.get("text") or ""), max_length=600)
                if text_out:
                    dur = max(1, min(300, len(raw_audio) // 4000 or 6))
                    # Whisper Large v3 Turbo reference cost ($0.04 / hour)
                    cost = ((Decimal(dur) / Decimal("3600")) * Decimal("0.04")).quantize(
                        Decimal("0.000001")
                    )
                    return TranscriptionResult(
                        provider="groq_whisper_large_v3_turbo",
                        model_name=stt_model,
                        transcript=text_out,
                        language_detected=language_locale,
                        duration_seconds=dur,
                        latency_ms=max(1, int((time.monotonic() - started) * 1000)),
                        estimated_cost_usd=cost,
                        fallback_used=False,
                    )
        except Exception:
            pass

        return self._fallback.transcribe_audio(
            audio_base64=audio_base64,
            audio_format=audio_format,
            language_locale=language_locale,
            fallback_transcript=fallback_transcript,
        )


def generate_livekit_room_token(
    settings: Settings,
    *,
    business_id: UUID,
    user_id: UUID,
    room_name: str,
) -> dict[str, Any]:
    """Mint a tenant-scoped LiveKit WebRTC access token when LIVEKIT_* env vars are configured."""
    lk_url = (settings.livekit_url or os.environ.get("LIVEKIT_URL", "")).strip()
    lk_key = (settings.livekit_api_key or os.environ.get("LIVEKIT_API_KEY", "")).strip()
    lk_secret = (
        settings.livekit_api_secret.get_secret_value().strip()
        if settings.livekit_api_secret
        else os.environ.get("LIVEKIT_API_SECRET", "").strip()
    )
    scoped_room = f"lp-{str(business_id)[:8]}-{room_name}"
    if not (lk_url and lk_key and lk_secret):
        return {
            "livekit_enabled": False,
            "mode": "groq_whisper_and_browser_tts",
            "room_name": scoped_room,
            "ws_url": "",
            "token": "",
        }

    now_epoch = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": lk_key,
        "sub": str(user_id),
        "nbf": now_epoch,
        "exp": now_epoch + 900,
        "name": f"member-{str(user_id)[:8]}",
        "metadata": json.dumps({"business_id": str(business_id), "read_only_tools": True}),
        "video": {
            "room": scoped_room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    )
    sig = hmac.new(
        lk_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    jwt_token = f"{signing_input}.{_b64url(sig)}"
    return {
        "livekit_enabled": True,
        "mode": "livekit_webrtc_agent",
        "room_name": scoped_room,
        "ws_url": lk_url,
        "token": jwt_token,
    }
