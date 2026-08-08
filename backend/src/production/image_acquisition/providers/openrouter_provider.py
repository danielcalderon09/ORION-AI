"""OpenRouter dedicated Image API adapter."""

import asyncio
import base64
import binascii
import io
import json
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError

from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderAuthenticationException,
    ImageAcquisitionProviderConfigurationException,
    ImageAcquisitionProviderContractException,
    ImageAcquisitionProviderModelException,
    ImageAcquisitionProviderPolicyException,
    ImageAcquisitionProviderRateLimitException,
    ImageAcquisitionProviderResponseException,
    ImageAcquisitionProviderUnavailableException,
    ImageAcquisitionProviderUncertainException,
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
        max_transport_attempts: int = 1,
        retry_base_delay_seconds: float = 1.0,
        max_response_bytes: int = 40_000_000,
        max_decoded_image_bytes: int = 25_000_000,
        provider_only: str | None = None,
        http_referer: str | None = None,
        app_title: str | None = None,
        client: httpx.AsyncClient | None = None,
        owns_client: bool = False,
        sleeper: Sleeper = asyncio.sleep,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ImageAcquisitionProviderConfigurationException(
                "image provider credential or model is missing"
            )
        parsed = httpx.URL(base_url)
        if (
            parsed.scheme != "https"
            or parsed.host != "openrouter.ai"
            or parsed.userinfo
            or parsed.path.rstrip("/") != "/api/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ImageAcquisitionProviderConfigurationException(
                "image provider base URL is invalid"
            )
        if timeout_seconds <= 0 or retry_base_delay_seconds <= 0:
            raise ImageAcquisitionProviderConfigurationException(
                "image provider timeout and retry delay must be positive"
            )
        if max_transport_attempts != 1:
            raise ImageAcquisitionProviderConfigurationException(
                "billable image provider permits exactly one transport attempt"
            )
        if not 1 <= max_response_bytes <= 100_000_000:
            raise ImageAcquisitionProviderConfigurationException(
                "image provider response limit is invalid"
            )
        if not 1 <= max_decoded_image_bytes <= 250_000_000:
            raise ImageAcquisitionProviderConfigurationException("decoded image limit is invalid")
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
        self._owns_client = client is None or owns_client
        self._closed = False
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
            "resolution": "1K",
            "aspect_ratio": asset.aspect_ratio,
            "quality": request.configuration.quality,
            "output_format": request.configuration.output_format,
            "stream": False,
            "provider": provider,
        }
        started = self._monotonic()
        body, request_id, http_status = await self._post_once(payload)
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        try:
            images = self._decode_images(
                body,
                expected_width=asset.width,
                expected_height=asset.height,
            )
        except ImageAcquisitionProviderResponseException as exc:
            exc.http_status = http_status
            exc.provider_request_id = request_id
            raise
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        return ImageAcquisitionProviderResponse(
            images=images,
            provider="openrouter",
            requested_model=self._model,
            reported_model=_safe_string(body.get("model")),
            request_id=request_id,
            input_tokens=_safe_int(usage.get("prompt_tokens", usage.get("input_tokens"))),
            output_tokens=_safe_int(usage.get("completion_tokens", usage.get("output_tokens"))),
            total_tokens=_safe_int(usage.get("total_tokens")),
            cost_usd=_safe_decimal(usage.get("cost")),
            http_status=http_status,
            latency_ms=latency_ms,
            finish_reason=_safe_string(body.get("finish_reason")),
            metadata={
                "prompt_version": prompt.version,
                "prompt_bytes": prompt.size_bytes,
                "prompt_sha256": prompt.sha256,
                "simulated": False,
            },
        )

    async def _post_once(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None, int]:
        try:
            async with self._get_client().stream(
                "POST",
                f"{self._base_url}/images",
                json=payload,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                content = await _read_bounded(response, self._max_response_bytes)
                request_id = _safe_remote_id(response.headers.get("x-request-id"))
                if response.status_code not in range(200, 300):
                    self._raise_http_error(response.status_code, content, request_id)
                try:
                    body = _load_strict_object(content)
                except ImageAcquisitionProviderResponseException as exc:
                    exc.http_status = response.status_code
                    exc.provider_request_id = request_id
                    raise
                request_id = request_id or _safe_remote_id(body.get("id"))
                return body, request_id, response.status_code
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageAcquisitionProviderUncertainException(
                "image provider submission outcome is uncertain"
            ) from exc
        except httpx.RequestError as exc:
            raise ImageAcquisitionProviderUncertainException(
                "image provider submission outcome is uncertain"
            ) from exc

    @staticmethod
    def _raise_http_error(
        status: int, content: bytes, request_id: str | None
    ) -> None:
        error_type = _safe_error_type(content)
        if status in {401, 402, 403} or error_type in {
            "authentication",
            "permission_denied",
            "payment_required",
        }:
            raise ImageAcquisitionProviderAuthenticationException(
                "image provider rejected authentication or billing authorization",
                http_status=status,
                provider_request_id=request_id,
            )
        if status == 429 or error_type == "rate_limit_exceeded":
            raise ImageAcquisitionProviderRateLimitException(
                "image provider rate limit reached",
                http_status=status,
                provider_request_id=request_id,
            )
        if status in _TRANSIENT_STATUS or error_type in {
            "provider_overloaded",
            "provider_unavailable",
            "server",
            "timeout",
            "unmapped",
        }:
            raise ImageAcquisitionProviderUnavailableException(
                "image provider is temporarily unavailable",
                http_status=status,
                provider_request_id=request_id,
            )
        if error_type in {"content_policy_violation", "refusal"}:
            raise ImageAcquisitionProviderPolicyException(
                "image request was rejected by content policy",
                http_status=status,
                provider_request_id=request_id,
            )
        if status == 404 or error_type in {"not_found", "model_not_found"}:
            raise ImageAcquisitionProviderModelException(
                "configured image model is unavailable or incompatible",
                http_status=status,
                provider_request_id=request_id,
            )
        raise ImageAcquisitionProviderContractException(
            "image provider rejected the request contract",
            http_status=status,
            provider_request_id=request_id,
        )

    def _decode_images(
        self,
        body: dict[str, Any],
        *,
        expected_width: int,
        expected_height: int,
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
        actual_media_type = _detect_image_mime(content)
        if actual_media_type is None or (
            media_type is not None and media_type != actual_media_type
        ):
            raise ImageAcquisitionProviderResponseException(
                "image provider media signature is invalid"
            )
        try:
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                width, height = decoded.size
                decoded_format = (decoded.format or "").upper()
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
            raise ImageAcquisitionProviderResponseException(
                "image provider returned an undecodable image"
            ) from exc
        expected_format = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/webp": "WEBP",
        }[actual_media_type]
        if decoded_format != expected_format or width <= 0 or height <= 0:
            raise ImageAcquisitionProviderResponseException(
                "image provider image dimensions or format are invalid"
            )
        if width * height > 40_000_000:
            raise ImageAcquisitionProviderResponseException(
                "image provider image dimensions exceed the configured limit"
            )
        expected_ratio = expected_width / expected_height
        actual_ratio = width / height
        if abs(actual_ratio - expected_ratio) / expected_ratio > 0.03:
            raise ImageAcquisitionProviderResponseException(
                "image provider image aspect ratio is outside tolerance"
            )
        return (
            GeneratedImagePayload(
                content=content,
                mime_type=actual_media_type,
                index=0,
                provider_metadata={"width": width, "height": height},
            ),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise ImageAcquisitionProviderConfigurationException(
                "image acquisition provider is closed"
            )
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
            )
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
        raise ImageAcquisitionProviderConfigurationException("HTTP-Referer is invalid")
    return referer


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_remote_id(value: Any) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else None


def _detect_image_mime(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


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
