"""OpenRouter adapter for one-shot structured narration fitting."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field, ValidationError

from backend.src.production.domain.base import ContractModel
from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleAuthenticationError,
    OpenAICompatibleProtocolError,
    OpenAICompatibleProtocolErrorCode,
    OpenAICompatibleRateLimitError,
    OpenAICompatibleResponsesClient,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleUnavailableError,
    load_strict_json_object,
)
from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingConfigurationError,
    NarrationFittingProviderError,
    NarrationFittingRequest,
    NarrationFittingResult,
    NarrationFittingTransientProviderError,
    validate_narration_revision,
)


class _RevisionEnvelope(ContractModel):
    narration: str = Field(min_length=3, max_length=6_000)


class OpenRouterNarrationFittingProvider:
    """Submit once; the enclosing speech manifest owns durable checkpoints."""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_output_tokens: int,
        temperature: float,
        max_response_bytes: int,
        max_provider_retries: int = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if (
            not model.strip()
            or max_output_tokens < 32
            or not 0 <= temperature <= 1
            or not 0 <= max_provider_retries <= 1
        ):
            raise NarrationFittingConfigurationError("narration fitting provider is invalid")
        self.model = model.strip()
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._max_provider_retries = max_provider_retries
        try:
            self._transport = OpenAICompatibleResponsesClient(
                api_key=api_key,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_transport_attempts=1,
                retry_base_delay_seconds=0.25,
                client=client,
                owns_client=True,
                max_response_bytes=max_response_bytes,
                sleeper=asyncio.sleep,
            )
        except ValueError as exc:
            raise NarrationFittingConfigurationError(
                "narration fitting HTTP config is invalid"
            ) from exc

    async def revise(self, request: NarrationFittingRequest) -> NarrationFittingResult:
        maximum_retries = min(self._max_provider_retries, request.maximum_provider_retries)
        for retry_number in range(maximum_retries + 1):
            try:
                return await self._revise_once(request, provider_retry_count=retry_number)
            except NarrationFittingTransientProviderError:
                if retry_number >= maximum_retries:
                    raise
                await asyncio.sleep(0.25)
        raise AssertionError("narration fitting retry loop did not return")

    async def _revise_once(
        self,
        request: NarrationFittingRequest,
        *,
        provider_retry_count: int,
    ) -> NarrationFittingResult:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Revise solo la narración indicada. Devuelve JSON estricto. "
                        "Preserva idioma, hechos, nombres, tono y significado; elimina "
                        "palabras secundarias para acercarse al tiempo objetivo."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "scene_id": request.scene_id,
                            "language": request.language,
                            "tone": request.tone,
                            "measured_duration_ms": request.current_duration_ms,
                            "target_duration_ms": request.target_duration_ms,
                            "narration": request.current_narration,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "orion_narration_revision",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"narration": {"type": "string", "minLength": 3}},
                        "required": ["narration"],
                    },
                },
            },
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        try:
            response = await self._transport.post_with_metadata(payload)
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleTimeoutError as exc:
            raise NarrationFittingTransientProviderError(
                "narration fitting request timed out",
                safe_error_code="timeout",
                retryable=True,
                provider_retry_count=provider_retry_count,
            ) from exc
        except OpenAICompatibleUnavailableError as exc:
            status = getattr(exc, "status_code", None)
            code = "http_5xx" if status is not None and status >= 500 else "connect_error"
            raise NarrationFittingTransientProviderError(
                "narration fitting provider is temporarily unavailable",
                safe_error_code=code,
                retryable=True,
                http_status=status,
                provider_request_id=getattr(exc, "request_id", None),
                response_headers_received=status is not None,
                provider_retry_count=provider_retry_count,
            ) from exc
        except OpenAICompatibleRateLimitError as exc:
            raise NarrationFittingTransientProviderError(
                "narration fitting provider rate limit reached",
                safe_error_code="http_429",
                retryable=True,
                http_status=getattr(exc, "status_code", None),
                provider_request_id=getattr(exc, "request_id", None),
                response_headers_received=getattr(exc, "status_code", None) is not None,
                provider_retry_count=provider_retry_count,
            ) from exc
        except OpenAICompatibleAuthenticationError as exc:
            raise NarrationFittingProviderError(
                "narration fitting authentication rejected",
                safe_error_code="http_401_403",
                http_status=getattr(exc, "status_code", None),
                provider_request_id=getattr(exc, "request_id", None),
                response_headers_received=getattr(exc, "status_code", None) is not None,
                provider_retry_count=provider_retry_count,
            ) from exc
        except OpenAICompatibleProtocolError as exc:
            raise _protocol_provider_error(exc, provider_retry_count) from exc
        try:
            text = self._transport.extract_single_output_text(response.body)
            envelope = _RevisionEnvelope.model_validate(load_strict_json_object(text))
        except OpenAICompatibleProtocolError as exc:
            raise _protocol_provider_error(
                exc,
                provider_retry_count,
                response_received=True,
                response_headers_received=True,
            ) from exc
        except (ValidationError, ValueError) as exc:
            code = "contract_error" if isinstance(exc, ValidationError) else "invalid_json"
            raise NarrationFittingProviderError(
                "narration fitting response failed validation",
                safe_error_code=code,
                retryable=False,
                http_status=response.http_status,
                provider_request_id=response.request_id,
                response_headers_received=True,
                response_received=True,
                provider_retry_count=provider_retry_count,
            ) from exc
        try:
            revised = validate_narration_revision(
                request.current_narration,
                envelope.narration,
            )
        except NarrationFittingProviderError as exc:
            raise NarrationFittingProviderError(
                "narration fitting semantic validation failed",
                safe_error_code="semantic_validation",
                retryable=False,
                http_status=response.http_status,
                provider_request_id=response.request_id,
                response_headers_received=True,
                response_received=True,
                provider_retry_count=provider_retry_count,
            ) from exc
        usage = response.body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return NarrationFittingResult(
            revised_narration=revised,
            provider=self.name,
            model=(self._transport.safe_string(response.body.get("model")) or self.model),
            http_status=response.http_status,
            provider_request_id=response.request_id,
            input_tokens=self._transport.safe_int(usage.get("prompt_tokens")),
            output_tokens=self._transport.safe_int(usage.get("completion_tokens")),
            total_tokens=self._transport.safe_int(usage.get("total_tokens")),
            reported_cost_usd=_reported_cost(usage),
            finish_reason=self._transport.extract_finish_reason(response.body),
            provider_retry_count=provider_retry_count,
        )

    async def close(self) -> None:
        await self._transport.close()


def _protocol_provider_error(
    error: OpenAICompatibleProtocolError,
    provider_retry_count: int,
    *,
    response_received: bool = False,
    response_headers_received: bool = False,
) -> NarrationFittingProviderError:
    if error.diagnostic_code is OpenAICompatibleProtocolErrorCode.HTTP_STATUS:
        code = (
            f"http_{error.status_code}"
            if error.status_code is not None
            else "http_error"
        )
    elif error.diagnostic_code is OpenAICompatibleProtocolErrorCode.RESPONSE_JSON:
        code = "invalid_json"
    else:
        code = "contract_error"
    return NarrationFittingProviderError(
        "narration fitting provider response is invalid",
        safe_error_code=code,
        retryable=False,
        http_status=error.status_code,
        provider_request_id=error.request_id,
        response_headers_received=response_headers_received or error.status_code is not None,
        response_received=response_received,
        provider_retry_count=provider_retry_count,
    )


def _reported_cost(usage: dict[str, Any]) -> Decimal | None:
    value = usage.get("cost")
    if isinstance(value, Decimal) and value >= 0:
        return value
    if isinstance(value, int) and value >= 0:
        return Decimal(value)
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
            return parsed if parsed >= 0 else None
        except Exception:
            return None
    return None


__all__ = ["OpenRouterNarrationFittingProvider"]
