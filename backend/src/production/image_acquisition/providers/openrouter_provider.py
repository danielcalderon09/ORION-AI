"""OpenRouter dedicated Image API adapter."""

import asyncio
import base64
import binascii
import json
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any

import httpx

from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderAuthenticationException,
    ImageAcquisitionProviderConfigurationException,
    ImageAcquisitionProviderContractException,
    ImageAcquisitionProviderModelException,
    ImageAcquisitionProviderPolicyException,
    ImageAcquisitionProviderRateLimitException,
    ImageAcquisitionProviderResponseException,
    ImageAcquisitionProviderTimeoutException,
    ImageAcquisitionProviderUnavailableException,
)
from backend.src.production.image_acquisition.ports import (
    GeneratedImagePayload,
    ImageAcquisitionProviderRequest,
    ImageAcquisitionProviderResponse,
)
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)

Sleeper = Callable[[float], Awaitable[None]]
_BASE64 = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class OpenRouterImageAcquisitionProvider:
    """Bounded non-streaming client for POST /api/v1/images."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_builder: ImageGenerationPromptBuilder,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 120,
        max_transport_attempts: int = 2,
        retry_base_delay_seconds: float = 1.0,
        max_response_bytes: int = 40_000_000,
        max_decoded_image_bytes: int = 25_000_000,
        provider_only: str | None = None,
        http_referer: str | None = None,
        app_title: str | None = None,
        client: httpx.AsyncClient | None = None,
        sleeper: Sleeper = asyncio.sleep,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ImageAcquisitionProviderConfigurationException(
                "image provider credential or model is missing"
            )
        parsed = httpx.URL(base_url)
        if parsed.scheme != "https" or not parsed.host or parsed.userinfo:
            raise ImageAcquisitionProviderConfigurationException(
                "image provider base URL is invalid"
            )
        if timeout_seconds <= 0 or retry_base_delay_seconds <= 0:
            raise ImageAcquisitionProviderConfigurationException(
                "image provider timeout and retry delay must be positive"
            )
        if not 1 <= max_transport_attempts <= 5:
            raise ImageAcquisitionProviderConfigurationException(
                "image provider transport attempts must be between 1 and 5"
            )
        if not 1 <= max_response_bytes <= 100_000_000:
            raise ImageAcquisitionProviderConfigurationException(
                "image provider response limit is invalid"
            )
        if not 1 <= max_decoded_image_bytes <= 250_000_000:
            raise ImageAcquisitionProviderConfigurationException(
                "decoded image limit is invalid"
            )
        if provider_only is not None and not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,99}",
            provider_only,
        ):
            raise ImageAcquisitionProviderConfigurationException(
                "image provider routing slug is invalid"
            )
        self._model = model.strip()
        self._prompt_builder = prompt_builder
        self._base_url = str(parsed).rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._attempts = max_transport_attempts
        self._retry_delay = retry_base_delay_seconds
        self._max_response_bytes = max_response_bytes
        self._max_decoded_bytes = max_decoded_image_bytes
        self._provider_only = provider_only
        self._client = client
        self._sleeper = sleeper
        self._monotonic = monotonic_clock
        self._headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        }
        if http_referer is not None:
            self._headers["HTTP-Referer"] = _validate_referer(http_referer)
        if app_title is not None:
            self._headers["X-Title"] = _validate_header(
                app_title,
                name="X-Title",
                maximum=200,
            )

    async def generate_image(
        self,
        request: ImageAcquisitionProviderRequest,
    ) -> ImageAcquisitionProviderResponse:
        prompt = self._prompt_builder.build(request)
        asset = request.visual_asset
        provider: dict[str, Any] = {"allow_fallbacks": False}
        if self._provider_only is not None:
            provider["only"] = [self._provider_only]
        payload = {
            "model": self._model,
            "prompt": prompt.text,
            "n": 1,
            "size": f"{asset.width}x{asset.height}",
            "aspect_ratio": asset.aspect_ratio,
            "quality": request.configuration.quality,
            "output_format": request.configuration.output_format,
            "stream": False,
            "provider": provider,
        }
        started = self._monotonic()
        body, request_id = await self._post_with_retries(payload)
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        images = self._decode_images(body)
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        return ImageAcquisitionProviderResponse(
            images=images,
            provider="openrouter",
            requested_model=self._model,
            reported_model=_safe_string(body.get("model")),
            request_id=request_id,
            input_tokens=_safe_int(
                usage.get("prompt_tokens", usage.get("input_tokens"))
            ),
            output_tokens=_safe_int(
                usage.get("completion_tokens", usage.get("output_tokens"))
            ),
            total_tokens=_safe_int(usage.get("total_tokens")),
            cost_usd=_safe_decimal(usage.get("cost")),
            latency_ms=latency_ms,
            finish_reason=_safe_string(body.get("finish_reason")),
            metadata={
                "prompt_version": prompt.version,
                "prompt_bytes": prompt.size_bytes,
                "prompt_sha256": prompt.sha256,
                "simulated": False,
            },
        )

    async def _post_with_retries(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        last_error: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                return await self._post_once(payload)
            except asyncio.CancelledError:
                raise
            except (
                ImageAcquisitionProviderTimeoutException,
                ImageAcquisitionProviderRateLimitException,
                ImageAcquisitionProviderUnavailableException,
            ) as exc:
                last_error = exc
            if attempt < self._attempts:
                await self._sleeper(self._retry_delay * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    async def _post_once(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        try:
            async with self._get_client().stream(
                "POST",
                f"{self._base_url}/images",
                json=payload,
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                content = await _read_bounded(response, self._max_response_bytes)
                if response.status_code not in range(200, 300):
                    self._raise_http_error(response.status_code, content)
                body = _load_strict_object(content)
                request_id = response.headers.get("x-request-id") or _safe_string(
                    body.get("id")
                )
                return body, request_id
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageAcquisitionProviderTimeoutException(
                "image provider request timed out"
            ) from exc
        except httpx.RequestError as exc:
            raise ImageAcquisitionProviderUnavailableException(
                "image provider connection failed"
            ) from exc

    @staticmethod
    def _raise_http_error(status: int, content: bytes) -> None:
        error_type = _safe_error_type(content)
        if status in {401, 402, 403} or error_type in {
            "authentication",
            "permission_denied",
            "payment_required",
        }:
            raise ImageAcquisitionProviderAuthenticationException(
                "image provider rejected authentication or billing authorization"
            )
        if status == 429 or error_type == "rate_limit_exceeded":
            raise ImageAcquisitionProviderRateLimitException(
                "image provider rate limit reached"
            )
        if status in _TRANSIENT_STATUS or error_type in {
            "provider_overloaded",
            "provider_unavailable",
            "server",
            "timeout",
            "unmapped",
        }:
            raise ImageAcquisitionProviderUnavailableException(
                "image provider is temporarily unavailable"
            )
        if error_type in {"content_policy_violation", "refusal"}:
            raise ImageAcquisitionProviderPolicyException(
                "image request was rejected by content policy"
            )
        if status == 404 or error_type in {"not_found", "model_not_found"}:
            raise ImageAcquisitionProviderModelException(
                "configured image model is unavailable or incompatible"
            )
        raise ImageAcquisitionProviderContractException(
            "image provider rejected the request contract"
        )

    def _decode_images(
        self,
        body: dict[str, Any],
    ) -> tuple[GeneratedImagePayload, ...]:
        data = body.get("data")
        if not isinstance(data, list) or len(data) != 1:
            raise ImageAcquisitionProviderResponseException(
                "image provider response must contain exactly one image"
            )
        item = data[0]
        if not isinstance(item, dict) or "url" in item:
            raise ImageAcquisitionProviderResponseException(
                "image provider must return embedded bytes, not a URL"
            )
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ImageAcquisitionProviderResponseException(
                "image provider response contains no image bytes"
            )
        if (
            encoded.startswith("data:")
            or any(char.isspace() for char in encoded)
            or not _BASE64.fullmatch(encoded)
            or len(encoded) > ((self._max_decoded_bytes + 2) // 3) * 4
        ):
            raise ImageAcquisitionProviderResponseException(
                "image provider returned invalid or oversized base64"
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageAcquisitionProviderResponseException(
                "image provider returned invalid base64"
            ) from exc
        if not content or len(content) > self._max_decoded_bytes:
            raise ImageAcquisitionProviderResponseException(
                "decoded image exceeds the configured limit"
            )
        media_type = item.get("media_type")
        if media_type is not None and media_type not in _MIME_TYPES:
            raise ImageAcquisitionProviderResponseException(
                "image provider returned an unsupported media type"
            )
        lowered_prefix = content[:256].lstrip().lower()
        if lowered_prefix.startswith((b"<svg", b"<?xml", b"<html", b"<!doctype")):
            raise ImageAcquisitionProviderResponseException(
                "image provider returned active or vector content"
            )
        return (
            GeneratedImagePayload(
                content=content,
                mime_type=media_type,
                index=0,
            ),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    result = bytearray()
    async for chunk in response.aiter_bytes():
        result.extend(chunk)
        if len(result) > maximum:
            raise ImageAcquisitionProviderResponseException(
                "image provider response exceeds the configured limit"
            )
    return bytes(result)


def _load_strict_object(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ImageAcquisitionProviderResponseException(
            "image provider returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ImageAcquisitionProviderResponseException(
            "image provider response must be a JSON object"
        )
    return value


def _safe_error_type(content: bytes) -> str | None:
    if len(content) > 64_000:
        return None
    try:
        body = _load_strict_object(content)
    except ImageAcquisitionProviderResponseException:
        return None
    direct = body.get("error_type")
    if isinstance(direct, str) and len(direct) <= 100:
        return direct
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    typed = error.get("type")
    if isinstance(typed, str) and len(typed) <= 100:
        return typed
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        typed = metadata.get("error_type")
        if isinstance(typed, str) and len(typed) <= 100:
            return typed
        provider_code = metadata.get("provider_code")
        if isinstance(provider_code, str) and len(provider_code) <= 100:
            return provider_code
    code = error.get("code")
    return code if isinstance(code, str) and len(code) <= 100 else None


def _validate_header(value: str, *, name: str, maximum: int) -> str:
    stripped = value.strip()
    if (
        not stripped
        or len(stripped) > maximum
        or any(ord(character) < 32 for character in stripped)
    ):
        raise ImageAcquisitionProviderConfigurationException(f"{name} is invalid")
    return stripped


def _validate_referer(value: str) -> str:
    referer = _validate_header(value, name="HTTP-Referer", maximum=2048)
    parsed = httpx.URL(referer)
    if parsed.scheme not in {"http", "https"} or not parsed.host or parsed.userinfo:
        raise ImageAcquisitionProviderConfigurationException(
            "HTTP-Referer is invalid"
        )
    return referer


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_int(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _safe_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, Decimal, str)):
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() and result >= 0 else None
    return None


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
