"""OpenRouter dedicated Image API adapter."""

import asyncio
import base64
import binascii
import io
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any, TypedDict

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from backend.src.production.image_acquisition.diagnostics import (
    ImageDiagnosticMetadata,
    ImageDiagnosticSubtype,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderAuthenticationException,
    ImageAcquisitionProviderConfigurationException,
    ImageAcquisitionProviderContractException,
    ImageAcquisitionProviderError,
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


class _ResponseContext(TypedDict):
    http_status: int
    provider_request_id: str | None
    requested_model: str
    reported_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: float
    finish_reason: str | None


class _HttpErrorContext(TypedDict):
    provider_request_id: str | None
    reported_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None
    finish_reason: str | None


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
        try:
            body, request_id, http_status = await self._post_once(payload)
        except ImageAcquisitionProviderError as exc:
            exc.requested_model = self._model
            exc.latency_ms = max(0.0, (self._monotonic() - started) * 1000)
            if exc.diagnostic_subtype is None:
                exc.diagnostic_subtype = _subtype_for_provider_exception(exc)
            raise
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        response_context: _ResponseContext = {
            "http_status": http_status,
            "provider_request_id": request_id,
            "requested_model": self._model,
            "reported_model": _safe_bounded_string(body.get("model"), 300),
            "input_tokens": _safe_int(usage.get("prompt_tokens", usage.get("input_tokens"))),
            "output_tokens": _safe_int(usage.get("completion_tokens", usage.get("output_tokens"))),
            "total_tokens": _safe_int(usage.get("total_tokens")),
            "cost_usd": _safe_decimal(usage.get("cost")),
            "latency_ms": latency_ms,
            "finish_reason": _safe_bounded_string(body.get("finish_reason"), 100),
        }
        if isinstance(body.get("error"), dict):
            raise ImageAcquisitionProviderResponseException(
                "image provider returned an error object in a successful response",
                diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_BODY_ERROR,
                **response_context,
            )
        try:
            images = self._decode_images(
                body,
                expected_width=asset.width,
                expected_height=asset.height,
                requested_output_format=request.configuration.output_format,
            )
        except ImageAcquisitionProviderResponseException as exc:
            _attach_response_context(exc, response_context)
            raise
        try:
            return ImageAcquisitionProviderResponse(
                images=images,
                provider="openrouter",
                requested_model=self._model,
                reported_model=_safe_string(body.get("model")),
                request_id=request_id,
                input_tokens=response_context["input_tokens"],
                output_tokens=response_context["output_tokens"],
                total_tokens=response_context["total_tokens"],
                cost_usd=response_context["cost_usd"],
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
        except ValidationError as exc:
            code, path, message = _sanitized_validation_error(exc)
            raise ImageAcquisitionProviderContractException(
                "image provider response metadata failed local validation",
                diagnostic_subtype=ImageDiagnosticSubtype.RESPONSE_MODEL_VALIDATION,
                diagnostic_metadata=ImageDiagnosticMetadata.model_validate(
                    images[0].provider_metadata.get("diagnostic", {})
                ),
                validation_error_code=code,
                validation_error_path=path,
                validation_error_message=message,
                **response_context,
            ) from exc

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
                request_id = _safe_remote_id(response.headers.get("x-request-id"))
                try:
                    content = await _read_bounded(response, self._max_response_bytes)
                except ImageAcquisitionProviderResponseException as exc:
                    exc.http_status = response.status_code
                    exc.provider_request_id = request_id
                    exc.diagnostic_subtype = ImageDiagnosticSubtype.PROVIDER_ENVELOPE
                    raise
                if response.status_code not in range(200, 300):
                    self._raise_http_error(response.status_code, content, request_id)
                try:
                    body = _load_strict_object(content)
                except ImageAcquisitionProviderResponseException as exc:
                    exc.http_status = response.status_code
                    exc.provider_request_id = request_id
                    exc.diagnostic_subtype = ImageDiagnosticSubtype.PROVIDER_ENVELOPE
                    raise
                request_id = request_id or _safe_remote_id(body.get("id"))
                return body, request_id, response.status_code
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageAcquisitionProviderUncertainException(
                "image provider submission outcome is uncertain",
                diagnostic_subtype=ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT,
            ) from exc
        except httpx.RequestError as exc:
            raise ImageAcquisitionProviderUncertainException(
                "image provider submission outcome is uncertain",
                diagnostic_subtype=ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT,
            ) from exc

    @staticmethod
    def _raise_http_error(status: int, content: bytes, request_id: str | None) -> None:
        body = _safe_optional_object(content)
        error_type = _safe_error_type(body)
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        context: _HttpErrorContext = {
            "provider_request_id": request_id or _safe_remote_id(body.get("id")),
            "reported_model": _safe_bounded_string(body.get("model"), 300),
            "input_tokens": _safe_int(usage.get("prompt_tokens", usage.get("input_tokens"))),
            "output_tokens": _safe_int(usage.get("completion_tokens", usage.get("output_tokens"))),
            "total_tokens": _safe_int(usage.get("total_tokens")),
            "cost_usd": _safe_decimal(usage.get("cost")),
            "finish_reason": _safe_bounded_string(body.get("finish_reason"), 100),
        }
        if status in {401, 402, 403} or error_type in {
            "authentication",
            "permission_denied",
            "payment_required",
        }:
            raise ImageAcquisitionProviderAuthenticationException(
                "image provider rejected authentication or billing authorization",
                http_status=status,
                diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_AUTHENTICATION,
                **context,
            )
        if status == 429 or error_type == "rate_limit_exceeded":
            raise ImageAcquisitionProviderRateLimitException(
                "image provider rate limit reached",
                http_status=status,
                diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_RATE_LIMIT,
                **context,
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
                diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE,
                **context,
            )
        if error_type in {"content_policy_violation", "refusal"}:
            raise ImageAcquisitionProviderPolicyException(
                "image request was rejected by content policy",
                http_status=status,
                diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_POLICY,
                **context,
            )
        if status == 404 or error_type in {"not_found", "model_not_found"}:
            raise ImageAcquisitionProviderModelException(
                "configured image model is unavailable or incompatible",
                http_status=status,
                diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_MODEL,
                **context,
            )
        raise ImageAcquisitionProviderContractException(
            "image provider rejected the request contract",
            http_status=status,
            diagnostic_subtype=ImageDiagnosticSubtype.PROVIDER_HTTP_ERROR,
            **context,
        )

    def _decode_images(
        self,
        body: dict[str, Any],
        *,
        expected_width: int,
        expected_height: int,
        requested_output_format: str,
    ) -> tuple[GeneratedImagePayload, ...]:
        diagnostic = ImageDiagnosticMetadata(
            expected_width=expected_width,
            expected_height=expected_height,
            expected_aspect_ratio=expected_width / expected_height,
            requested_output_format=requested_output_format,
        )
        data = body.get("data")
        if not isinstance(data, list):
            raise _image_response_error(
                "image provider response contains no image list",
                ImageDiagnosticSubtype.MISSING_IMAGE,
                diagnostic,
            )
        if not data:
            raise _image_response_error(
                "image provider response contains no image",
                ImageDiagnosticSubtype.MISSING_IMAGE,
                diagnostic,
            )
        if len(data) != 1:
            raise _image_response_error(
                "image provider response contains multiple images",
                ImageDiagnosticSubtype.MULTIPLE_IMAGES,
                diagnostic,
            )
        item = data[0]
        if not isinstance(item, dict) or "url" in item:
            raise _image_response_error(
                "image provider image item has an invalid envelope",
                ImageDiagnosticSubtype.PROVIDER_ENVELOPE,
                diagnostic,
            )
        media_type = item.get("media_type")
        diagnostic = diagnostic.model_copy(
            update={
                "declared_media_type": media_type
                if isinstance(media_type, str) and len(media_type) <= 100
                else None,
            }
        )
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise _image_response_error(
                "image provider response contains no image bytes",
                ImageDiagnosticSubtype.MISSING_IMAGE,
                diagnostic,
            )
        if len(encoded) > ((self._max_decoded_bytes + 2) // 3) * 4:
            raise _image_response_error(
                "encoded image exceeds the configured limit",
                ImageDiagnosticSubtype.DECODED_IMAGE_TOO_LARGE,
                diagnostic,
            )
        if (
            encoded.startswith("data:")
            or any(char.isspace() for char in encoded)
            or not _BASE64.fullmatch(encoded)
        ):
            raise _image_response_error(
                "image provider returned invalid base64",
                ImageDiagnosticSubtype.INVALID_BASE64,
                diagnostic,
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _image_response_error(
                "image provider returned invalid base64",
                ImageDiagnosticSubtype.INVALID_BASE64,
                diagnostic,
            ) from exc
        if not content or len(content) > self._max_decoded_bytes:
            raise _image_response_error(
                "decoded image exceeds the configured limit",
                ImageDiagnosticSubtype.DECODED_IMAGE_TOO_LARGE,
                diagnostic,
            )
        diagnostic = diagnostic.model_copy(
            update={
                "decoded_size_bytes": len(content),
            }
        )
        if media_type is not None and media_type not in _MIME_TYPES:
            raise _image_response_error(
                "image provider returned an unsupported media type",
                ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT,
                diagnostic,
            )
        lowered_prefix = content[:256].lstrip().lower()
        if lowered_prefix.startswith((b"<svg", b"<?xml", b"<html", b"<!doctype")):
            raise _image_response_error(
                "image provider returned active or vector content",
                ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT,
                diagnostic,
            )
        actual_media_type = _detect_image_mime(content)
        diagnostic = diagnostic.model_copy(update={"detected_media_type": actual_media_type})
        if actual_media_type is None:
            raise _image_response_error(
                "image provider media signature is invalid",
                ImageDiagnosticSubtype.INVALID_IMAGE_SIGNATURE,
                diagnostic,
            )
        if media_type is not None and media_type != actual_media_type:
            raise _image_response_error(
                "declared image MIME differs from detected signature",
                ImageDiagnosticSubtype.MIME_MISMATCH,
                diagnostic,
            )
        try:
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                width, height = decoded.size
                decoded_format = (decoded.format or "").upper()
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
            raise _image_response_error(
                "image provider returned an undecodable image",
                ImageDiagnosticSubtype.UNDECODABLE_IMAGE,
                diagnostic,
            ) from exc
        diagnostic = diagnostic.model_copy(
            update={
                "decoded_width": width if width > 0 else None,
                "decoded_height": height if height > 0 else None,
                "decoded_format": decoded_format[:20] or None,
                "actual_aspect_ratio": width / height if width > 0 and height > 0 else None,
            }
        )
        expected_format = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/webp": "WEBP",
        }[actual_media_type]
        if decoded_format != expected_format or width <= 0 or height <= 0:
            subtype = (
                ImageDiagnosticSubtype.INVALID_DIMENSIONS
                if width <= 0 or height <= 0
                else ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT
            )
            raise _image_response_error(
                "image provider image dimensions or format are invalid",
                subtype,
                diagnostic,
            )
        if width * height > 40_000_000:
            raise _image_response_error(
                "image provider image dimensions exceed the configured limit",
                ImageDiagnosticSubtype.DECODED_IMAGE_TOO_LARGE,
                diagnostic,
            )
        expected_ratio = expected_width / expected_height
        actual_ratio = width / height
        if abs(actual_ratio - expected_ratio) / expected_ratio > 0.03:
            raise _image_response_error(
                "image provider image aspect ratio is outside tolerance",
                ImageDiagnosticSubtype.ASPECT_RATIO_MISMATCH,
                diagnostic,
            )
        return (
            GeneratedImagePayload(
                content=content,
                mime_type=actual_media_type,
                index=0,
                provider_metadata={
                    "width": width,
                    "height": height,
                    "diagnostic": diagnostic.model_dump(mode="json"),
                },
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


def _safe_optional_object(content: bytes) -> dict[str, Any]:
    if len(content) > 64_000:
        return {}
    try:
        return _load_strict_object(content)
    except ImageAcquisitionProviderResponseException:
        return {}


def _safe_error_type(body: dict[str, Any]) -> str | None:
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


def _safe_bounded_string(value: Any, maximum: int) -> str | None:
    return value if isinstance(value, str) and 1 <= len(value) <= maximum else None


def _image_response_error(
    message: str,
    subtype: ImageDiagnosticSubtype,
    metadata: ImageDiagnosticMetadata,
) -> ImageAcquisitionProviderResponseException:
    return ImageAcquisitionProviderResponseException(
        message,
        diagnostic_subtype=subtype,
        diagnostic_metadata=metadata,
        validation_error_code=subtype.value,
        validation_error_path="data[0]",
        validation_error_message=message[:500],
    )


def _attach_response_context(
    error: ImageAcquisitionProviderError,
    context: Mapping[str, Any],
) -> None:
    for name, value in context.items():
        if getattr(error, name, None) is None:
            setattr(error, name, value)


def _subtype_for_provider_exception(
    error: ImageAcquisitionProviderError,
) -> ImageDiagnosticSubtype:
    if isinstance(error, ImageAcquisitionProviderAuthenticationException):
        return ImageDiagnosticSubtype.PROVIDER_AUTHENTICATION
    if isinstance(error, ImageAcquisitionProviderRateLimitException):
        return ImageDiagnosticSubtype.PROVIDER_RATE_LIMIT
    if isinstance(error, ImageAcquisitionProviderUnavailableException):
        return ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE
    if isinstance(error, ImageAcquisitionProviderPolicyException):
        return ImageDiagnosticSubtype.PROVIDER_POLICY
    if isinstance(error, ImageAcquisitionProviderModelException):
        return ImageDiagnosticSubtype.PROVIDER_MODEL
    if isinstance(error, ImageAcquisitionProviderContractException):
        return ImageDiagnosticSubtype.PROVIDER_CONTRACT
    if isinstance(error, ImageAcquisitionProviderUncertainException):
        return ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT
    if isinstance(error, ImageAcquisitionProviderResponseException):
        return ImageDiagnosticSubtype.PROVIDER_ENVELOPE
    return ImageDiagnosticSubtype.UNKNOWN_IMAGE_ERROR


def _sanitized_validation_error(
    error: ValidationError,
) -> tuple[str, str, str]:
    first = error.errors(include_url=False, include_context=False, include_input=False)[0]
    raw_type = str(first.get("type", "validation_error"))
    code = re.sub(r"[^a-z0-9_]+", "_", raw_type.lower())[:100] or "validation_error"
    location = first.get("loc", ())
    path = ".".join(str(part) for part in location)[:300] or "response"
    message = " ".join(str(first.get("msg", "response validation failed")).split())[:500]
    return code, path, message


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
