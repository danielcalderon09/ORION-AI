"""Operation-aware, non-sensitive HTTP error classification for OpenRouter video."""

from __future__ import annotations

import hashlib
import json
import re

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoAuthenticationError,
    OpenRouterVideoError,
    OpenRouterVideoInsufficientCreditsError,
    OpenRouterVideoInvalidRequestError,
    OpenRouterVideoPermissionError,
    OpenRouterVideoRateLimitError,
    OpenRouterVideoServerError,
    OpenRouterVideoTransportError,
)

_SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def raise_for_openrouter_status(
    status: int,
    *,
    operation: str,
    response_body: bytes | None = None,
) -> None:
    if 200 <= status < 300:
        return
    message = f"OpenRouter video {operation} failed with HTTP {status}"
    error: OpenRouterVideoError
    if status == 400 or status in {404, 409, 422}:
        error = OpenRouterVideoInvalidRequestError(message)
    elif status == 401:
        error = OpenRouterVideoAuthenticationError(message)
    elif status == 402:
        error = OpenRouterVideoInsufficientCreditsError(message)
    elif status == 403:
        error = OpenRouterVideoPermissionError(message)
    elif status == 429:
        error = OpenRouterVideoRateLimitError(message)
    elif status >= 500:
        error = OpenRouterVideoServerError(message)
    else:
        error = OpenRouterVideoTransportError(message)
    error.http_status = status
    if operation != "discovery":
        diagnostic_code, metadata = _safe_http_diagnostic(
            status=status,
            operation=operation,
            response_body=response_body,
        )
        error.add_diagnostic(
            phase=f"provider_{operation}",
            code=diagnostic_code,
            metadata=metadata,
        )
    raise error


def _safe_http_diagnostic(
    *,
    status: int,
    operation: str,
    response_body: bytes | None,
) -> tuple[str, dict[str, object]]:
    metadata: dict[str, object] = {"provider_http_status": status}
    if response_body is not None:
        metadata["provider_error_body_bytes"] = len(response_body)
        metadata["provider_error_body_sha256"] = hashlib.sha256(response_body).hexdigest()

    provider_code, provider_message = _provider_error_fields(response_body)
    if provider_code is not None:
        metadata["provider_error_code"] = provider_code

    searchable = " ".join(value for value in (provider_code, provider_message) if value).lower()
    if "zero data retention" in searchable or re.search(r"\bzdr\b", searchable):
        leaf = "video_provider_zdr_incompatible"
    elif (
        any(token in searchable for token in ("image", "frame", "asset"))
        and any(
            token in searchable
            for token in ("access", "download", "fetch", "reachable", "url")
        )
    ):
        leaf = "video_reference_asset_invalid"
    elif "duration" in searchable:
        leaf = "video_duration_invalid"
    elif "resolution" in searchable or "aspect ratio" in searchable:
        leaf = "video_request_dimensions_invalid"
    elif "model" in searchable:
        leaf = "video_provider_model_invalid"
    else:
        leaf = "video_provider_http_error"
    metadata["provider_operation"] = operation
    return leaf, metadata


def _provider_error_fields(response_body: bytes | None) -> tuple[str | None, str | None]:
    if response_body is None:
        return None, None
    try:
        parsed = json.loads(response_body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    error = parsed.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
    else:
        code = parsed.get("code")
        message = error if isinstance(error, str) else parsed.get("message")
    safe_code = str(code) if isinstance(code, (str, int)) else None
    if safe_code is not None and _SAFE_PROVIDER_CODE.fullmatch(safe_code) is None:
        safe_code = None
    return safe_code, message if isinstance(message, str) else None
