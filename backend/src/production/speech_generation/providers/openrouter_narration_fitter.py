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
    NarrationFittingUncertainError,
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
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model.strip() or max_output_tokens < 32 or not 0 <= temperature <= 1:
            raise NarrationFittingConfigurationError("narration fitting provider is invalid")
        self.model = model.strip()
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
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
            text = self._transport.extract_single_output_text(response.body)
            envelope = _RevisionEnvelope.model_validate(load_strict_json_object(text))
            revised = validate_narration_revision(
                request.current_narration,
                envelope.narration,
            )
        except asyncio.CancelledError:
            raise
        except (OpenAICompatibleTimeoutError, OpenAICompatibleUnavailableError) as exc:
            raise NarrationFittingUncertainError(
                "narration fitting submission is uncertain"
            ) from exc
        except (
            OpenAICompatibleAuthenticationError,
            OpenAICompatibleRateLimitError,
            OpenAICompatibleProtocolError,
            NarrationFittingProviderError,
            ValidationError,
            ValueError,
        ) as exc:
            raise NarrationFittingProviderError(
                "narration fitting provider response is invalid"
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
        )

    async def close(self) -> None:
        await self._transport.close()


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
