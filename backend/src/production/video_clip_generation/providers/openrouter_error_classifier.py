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

_KNOWN_ERROR_TYPES = frozenset(
    {
        "content_policy_violation",
        "image_not_found",
        "image_too_large",
        "image_too_small",
        "invalid_image",
        "invalid_request",
        "payment_required",
        "permission_denied",
        "provider_unavailable",
        "unprocessable",
        "unsupported_image_format",
    }
)
_KNOWN_SAFE_CODES = frozenset(
    {
        *_KNOWN_ERROR_TYPES,
        "invalid_aspect_ratio",
        "invalid_dimensions",
        "invalid_duration",
        "invalid_image_reference",
        "invalid_model",
        "invalid_resolution",
        "model_not_found",
        "reference_image_unreachable",
        "unsupported_duration",
        "zdr_incompatible",
    }
)


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

    openrouter_code, provider_message, error_type, provider_code = (
        _provider_error_fields(response_body)
    )
    if openrouter_code is not None:
        metadata["openrouter_error_code"] = openrouter_code
        # Historical diagnostics used this name for the top-level OpenRouter code.
        metadata["provider_error_code"] = openrouter_code
    if error_type is not None:
        metadata["openrouter_error_type"] = error_type
    if provider_code is not None:
        metadata["openrouter_provider_code"] = provider_code

    reason = _provider_error_reason(
        status=status,
        openrouter_code=openrouter_code,
        error_type=error_type,
        provider_code=provider_code,
        provider_message=provider_message,
    )
    leaf = _leaf_code(reason)
    metadata["provider_error_reason"] = reason
    metadata["provider_operation"] = operation
    return leaf, metadata


def _provider_error_fields(
    response_body: bytes | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    if response_body is None:
        return None, None, None, None
    try:
        parsed = json.loads(response_body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None, None, None
    if not isinstance(parsed, dict):
        return None, None, None, None
    error = parsed.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        raw_metadata = error.get("metadata")
    else:
        code = parsed.get("code")
        message = error if isinstance(error, str) else parsed.get("message")
        raw_metadata = parsed.get("metadata")
    safe_code = _safe_provider_code(code)
    error_type: str | None = None
    provider_code: str | None = None
    if isinstance(raw_metadata, dict):
        error_type = _known_error_type(raw_metadata.get("error_type"))
        provider_code = _safe_provider_code(raw_metadata.get("provider_code"))
    return (
        safe_code,
        message if isinstance(message, str) else None,
        error_type,
        provider_code,
    )


def _known_error_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in _KNOWN_ERROR_TYPES else None


def _safe_provider_code(value: object) -> str | None:
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized.isdecimal() or normalized in _KNOWN_SAFE_CODES:
        return normalized
    return None


def _provider_error_reason(
    *,
    status: int,
    openrouter_code: str | None,
    error_type: str | None,
    provider_code: str | None,
    provider_message: str | None,
) -> str:
    structured = {
        "content_policy_violation": "content_policy",
        "image_not_found": "reference_asset_unreachable",
        "image_too_large": "reference_asset_invalid",
        "image_too_small": "reference_asset_invalid",
        "invalid_image": "reference_asset_invalid",
        "payment_required": "insufficient_credits",
        "permission_denied": "permission_denied",
        "provider_unavailable": "provider_unavailable",
        "unsupported_image_format": "reference_asset_invalid",
    }
    if error_type in structured:
        return structured[error_type]

    for value in (provider_code, provider_message, openrouter_code):
        reason = _reason_from_text(value)
        if reason is not None:
            return reason
    if error_type in {"invalid_request", "unprocessable"}:
        return "invalid_request"
    if status == 402:
        return "insufficient_credits"
    if status == 403:
        return "permission_denied"
    if status >= 500:
        return "provider_unavailable"
    if status in {400, 409, 422}:
        return "invalid_request"
    return "unknown"


def _reason_from_text(value: str | None) -> str | None:
    if value is None:
        return None
    searchable = value.lower()
    if "zero data retention" in searchable or re.search(r"\bzdr\b", searchable):
        return "zdr_incompatible"
    if (
        any(token in searchable for token in ("image", "frame", "asset"))
        and any(
            token in searchable
            for token in ("access", "download", "fetch", "not found", "reachable", "url")
        )
    ):
        return "reference_asset_unreachable"
    if any(token in searchable for token in ("image", "frame")) and any(
        token in searchable for token in ("format", "invalid", "large", "size", "small")
    ):
        return "reference_asset_invalid"
    if "duration" in searchable:
        return "invalid_duration"
    if "resolution" in searchable or "aspect ratio" in searchable:
        return "invalid_dimensions"
    if "model" in searchable:
        return "invalid_model"
    if any(token in searchable for token in ("credit", "payment", "billing")):
        return "insufficient_credits"
    if any(token in searchable for token in ("forbidden", "permission", "unauthorized")):
        return "permission_denied"
    if any(token in searchable for token in ("content policy", "moderation", "safety policy")):
        return "content_policy"
    if any(token in searchable for token in ("no endpoint", "overloaded", "unavailable")):
        return "provider_unavailable"
    if "bad request" in searchable or "invalid request" in searchable:
        return "invalid_request"
    return None


def _leaf_code(reason: str) -> str:
    return {
        "content_policy": "video_provider_content_policy",
        "insufficient_credits": "video_provider_insufficient_credits",
        "invalid_dimensions": "video_request_dimensions_invalid",
        "invalid_duration": "video_duration_invalid",
        "invalid_model": "video_provider_model_invalid",
        "permission_denied": "video_provider_permission_denied",
        "provider_unavailable": "video_provider_unavailable",
        "reference_asset_invalid": "video_reference_asset_invalid",
        "reference_asset_unreachable": "video_reference_asset_invalid",
        "zdr_incompatible": "video_provider_zdr_incompatible",
    }.get(reason, "video_provider_http_error")
