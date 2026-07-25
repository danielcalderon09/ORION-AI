"""Bounded OpenRouter model discovery with a monotonic TTL cache."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoCapabilityError,
    OpenRouterVideoInvalidResponseError,
    OpenRouterVideoResponseTooLargeError,
    OpenRouterVideoTimeoutError,
    OpenRouterVideoTransportError,
    OpenRouterVideoUnsupportedModelError,
)
from backend.src.production.video_clip_generation.providers.openrouter_error_classifier import (
    raise_for_openrouter_status,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterVideoModelCapability,
    OpenRouterVideoModelsResponse,
)


class OpenRouterVideoModelCapabilityResolver:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        max_response_bytes: int,
        cache_ttl_seconds: float,
        monotonic: Callable[[], float],
    ) -> None:
        self._client = client
        self._maximum = max_response_bytes
        self._ttl = cache_ttl_seconds
        self._monotonic = monotonic
        self._cached: OpenRouterVideoModelsResponse | None = None
        self._cached_at = 0.0

    async def resolve(
        self,
        *,
        model: str,
        duration: int,
        resolution: str,
        aspect_ratio: str,
    ) -> OpenRouterVideoModelCapability:
        models = await self._models()
        capability = next((item for item in models.data if item.id == model), None)
        if capability is None:
            raise OpenRouterVideoUnsupportedModelError(
                "configured OpenRouter video model is unavailable"
            )
        unsupported = (
            duration not in capability.supported_durations
            or resolution not in capability.supported_resolutions
            or aspect_ratio not in capability.supported_aspect_ratios
            or "first_frame" not in capability.supported_frame_images
        )
        if unsupported:
            raise OpenRouterVideoCapabilityError(
                "configured OpenRouter model does not support the closed request"
            )
        return capability

    async def _models(self) -> OpenRouterVideoModelsResponse:
        now = self._monotonic()
        if self._cached is not None and now - self._cached_at < self._ttl:
            return self._cached
        try:
            response = await self._client.get(
                "/api/v1/videos/models",
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise OpenRouterVideoTimeoutError(
                "OpenRouter video capability discovery timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise OpenRouterVideoTransportError(
                "OpenRouter video capability discovery failed"
            ) from exc
        raise_for_openrouter_status(response.status_code, operation="discovery")
        content = await _read_bounded(response, self._maximum)
        try:
            parsed = _strict_json(content)
            models = OpenRouterVideoModelsResponse.model_validate(parsed)
        except (UnicodeDecodeError, ValueError) as exc:
            raise OpenRouterVideoInvalidResponseError(
                "OpenRouter video capability response is invalid"
            ) from exc
        self._cached = models
        self._cached_at = now
        return models


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > maximum:
                raise OpenRouterVideoResponseTooLargeError(
                    "OpenRouter video response exceeds the configured limit"
                )
        except ValueError as exc:
            raise OpenRouterVideoInvalidResponseError(
                "OpenRouter video response length is invalid"
            ) from exc
    result = bytearray()
    async for chunk in response.aiter_bytes():
        if len(result) + len(chunk) > maximum:
            raise OpenRouterVideoResponseTooLargeError(
                "OpenRouter video response exceeds the configured limit"
            )
        result.extend(chunk)
    return bytes(result)


def _strict_json(content: bytes) -> object:
    return json.loads(
        content.decode("utf-8", errors="strict"),
        parse_float=str,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicates,
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
