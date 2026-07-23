"""OpenRouter scene-planning adapter over the shared neutral transport."""

import asyncio
from collections.abc import Callable
from time import monotonic

import httpx
from pydantic import ValidationError

from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleAuthenticationError,
    OpenAICompatibleProtocolError,
    OpenAICompatibleRateLimitError,
    OpenAICompatibleResponsesClient,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleUnavailableError,
    Sleeper,
    load_strict_json_object,
)
from backend.src.production.scene_planning.exceptions import (
    ScenePlanningProviderAuthenticationException,
    ScenePlanningProviderConfigurationException,
    ScenePlanningProviderContractException,
    ScenePlanningProviderRateLimitException,
    ScenePlanningProviderResponseException,
    ScenePlanningProviderTimeoutException,
    ScenePlanningProviderUnavailableException,
)
from backend.src.production.scene_planning.models import (
    ProductionScenePlan,
    validate_scene_plan_against_script,
)
from backend.src.production.scene_planning.ports import ScenePlanningProviderResponse
from backend.src.production.scene_planning.prompt_builder import ScenePlanningPromptBuilder
from backend.src.production.scripting.models import ProductionScript


class OpenRouterScenePlanningProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_builder: ScenePlanningPromptBuilder,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 30,
        max_transport_attempts: int = 2,
        retry_base_delay_seconds: float = 0.25,
        max_output_tokens: int = 8192,
        temperature: float = 0.2,
        http_referer: str | None = None,
        app_title: str | None = None,
        client: httpx.AsyncClient | None = None,
        sleeper: Sleeper = asyncio.sleep,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ScenePlanningProviderConfigurationException(
                "scene-planning provider credential or model is missing"
            )
        if not 1 <= max_transport_attempts <= 5:
            raise ScenePlanningProviderConfigurationException(
                "scene-planning transport attempts must be between 1 and 5"
            )
        if timeout_seconds <= 0 or retry_base_delay_seconds <= 0:
            raise ScenePlanningProviderConfigurationException(
                "scene-planning timeout and retry delay must be positive"
            )
        if max_output_tokens < 1 or not 0 <= temperature <= 2:
            raise ScenePlanningProviderConfigurationException(
                "scene-planning output token or temperature setting is invalid"
            )
        try:
            self._transport = OpenAICompatibleResponsesClient(
                api_key=api_key,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_transport_attempts=max_transport_attempts,
                retry_base_delay_seconds=retry_base_delay_seconds,
                http_referer=http_referer,
                app_title=app_title,
                client=client,
                sleeper=sleeper,
            )
        except ValueError as exc:
            raise ScenePlanningProviderConfigurationException(
                "scene-planning provider HTTP configuration is invalid"
            ) from exc
        self._model = model.strip()
        self._prompt_builder = prompt_builder
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._monotonic = monotonic_clock

    async def generate_scene_plan(
        self,
        script: ProductionScript,
    ) -> ScenePlanningProviderResponse:
        try:
            prompt = self._prompt_builder.build(script)
        except (TypeError, ValueError) as exc:
            raise ScenePlanningProviderContractException(
                "scene-planning prompt could not be constructed"
            ) from exc
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "production_scene_plan",
                    "strict": True,
                    "schema": prompt.response_schema,
                },
            },
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "max_tokens": self._max_output_tokens,
            "temperature": self._temperature,
            "store": False,
            "stream": False,
        }
        started = self._monotonic()
        try:
            body, request_id = await self._transport.post(payload)
            text = self._transport.extract_single_output_text(body)
        except OpenAICompatibleAuthenticationError as exc:
            raise ScenePlanningProviderAuthenticationException(
                "scene-planning provider rejected authentication"
            ) from exc
        except OpenAICompatibleRateLimitError as exc:
            raise ScenePlanningProviderRateLimitException(
                "scene-planning provider rate limit reached"
            ) from exc
        except OpenAICompatibleTimeoutError as exc:
            raise ScenePlanningProviderTimeoutException(
                "scene-planning request timed out"
            ) from exc
        except OpenAICompatibleUnavailableError as exc:
            raise ScenePlanningProviderUnavailableException(
                "scene-planning provider is unavailable"
            ) from exc
        except OpenAICompatibleProtocolError as exc:
            raise ScenePlanningProviderResponseException(
                "scene-planning provider returned an invalid response"
            ) from exc
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        try:
            plan = ProductionScenePlan.model_validate(load_strict_json_object(text))
            validate_scene_plan_against_script(plan, script)
        except (ValueError, ValidationError, TypeError) as exc:
            raise ScenePlanningProviderContractException(
                "scene-planning provider output failed contract validation"
            ) from exc
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        reported_model = self._transport.safe_string(body.get("model"))
        return ScenePlanningProviderResponse(
            scene_plan=plan,
            provider="openrouter",
            model=reported_model or self._model,
            requested_model=self._model,
            reported_model=reported_model,
            request_id=request_id,
            input_tokens=self._transport.safe_int(
                usage.get("prompt_tokens", usage.get("input_tokens"))
            ),
            output_tokens=self._transport.safe_int(
                usage.get("completion_tokens", usage.get("output_tokens"))
            ),
            total_tokens=self._transport.safe_int(usage.get("total_tokens")),
            latency_ms=latency_ms,
            finish_reason=self._transport.extract_finish_reason(body),
        )

    async def close(self) -> None:
        await self._transport.close()


class RealScenePlanningProvider(OpenRouterScenePlanningProvider):
    """Explicit real-provider name; OpenRouter is ORION's configured implementation."""

    __slots__ = ()
