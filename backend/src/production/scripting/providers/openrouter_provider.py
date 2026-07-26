"""OpenRouter scripting adapter over neutral OpenAI-compatible transport."""

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
from backend.src.production.scripting.exceptions import (
    ScriptingProviderAuthenticationError,
    ScriptingProviderConfigurationError,
    ScriptingProviderContractError,
    ScriptingProviderRateLimitError,
    ScriptingProviderResponseError,
    ScriptingProviderTimeoutError,
    ScriptingProviderUnavailableError,
)
from backend.src.production.scripting.models import (
    ProductionScript,
    validate_script_against_plan,
)
from backend.src.production.scripting.ports import (
    ScriptingProviderRequest,
    ScriptingProviderResponse,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder


class OpenRouterScriptingProvider:
    """Real scripting provider with no HTTP concepts in its public contract."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_builder: ScriptingPromptBuilder,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 30,
        max_transport_attempts: int = 2,
        retry_base_delay_seconds: float = 0.25,
        max_output_tokens: int = 8192,
        temperature: float = 0.2,
        http_referer: str | None = None,
        app_title: str | None = None,
        client: httpx.AsyncClient | None = None,
        owns_client: bool = False,
        sleeper: Sleeper = asyncio.sleep,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip():
            raise ScriptingProviderConfigurationError("scripting provider credential is missing")
        if not model.strip():
            raise ScriptingProviderConfigurationError("scripting model is missing")
        if not 1 <= max_transport_attempts <= 5:
            raise ScriptingProviderConfigurationError(
                "scripting transport attempts must be between 1 and 5"
            )
        if timeout_seconds <= 0 or retry_base_delay_seconds <= 0:
            raise ScriptingProviderConfigurationError(
                "scripting timeout and retry delay must be positive"
            )
        if max_output_tokens < 1 or not 0 <= temperature <= 2:
            raise ScriptingProviderConfigurationError(
                "scripting output token or temperature setting is invalid"
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
                owns_client=owns_client,
                sleeper=sleeper,
            )
        except ValueError as exc:
            raise ScriptingProviderConfigurationError(
                "scripting provider HTTP configuration is invalid"
            ) from exc
        self._model = model.strip()
        self._prompt_builder = prompt_builder
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._monotonic = monotonic_clock

    async def generate_script(self, request: ScriptingProviderRequest) -> ScriptingProviderResponse:
        try:
            prompt = self._prompt_builder.build(request)
        except (TypeError, ValueError) as exc:
            raise ScriptingProviderContractError(
                "scripting prompt could not be constructed"
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
                    "name": "production_script",
                    "strict": True,
                    "schema": prompt.response_schema,
                },
            },
            "provider": {
                "require_parameters": True,
                "data_collection": "deny",
            },
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
            raise ScriptingProviderAuthenticationError(
                "scripting provider rejected authentication"
            ) from exc
        except OpenAICompatibleRateLimitError as exc:
            raise ScriptingProviderRateLimitError("scripting provider rate limit reached") from exc
        except OpenAICompatibleTimeoutError as exc:
            raise ScriptingProviderTimeoutError("scripting request timed out") from exc
        except OpenAICompatibleUnavailableError as exc:
            raise ScriptingProviderUnavailableError("scripting provider is unavailable") from exc
        except OpenAICompatibleProtocolError as exc:
            raise ScriptingProviderResponseError(
                "scripting provider returned an invalid response"
            ) from exc
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        try:
            script = ProductionScript.model_validate(load_strict_json_object(text))
            validate_script_against_plan(script, request.plan)
        except (ValueError, ValidationError, TypeError) as exc:
            raise ScriptingProviderContractError(
                "scripting provider output failed contract validation"
            ) from exc
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        reported_model = self._transport.safe_string(body.get("model"))
        return ScriptingProviderResponse(
            script=script,
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
            metadata={},
        )

    async def close(self) -> None:
        await self._transport.close()
