"""OpenAI Responses API adapter implemented with the installed async HTTP client."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

import httpx
from pydantic import ValidationError

from backend.src.production.planning.exceptions import (
    PlanningProviderAuthenticationError,
    PlanningProviderConfigurationError,
    PlanningProviderContractError,
    PlanningProviderRateLimitError,
    PlanningProviderResponseError,
    PlanningProviderTimeoutError,
    PlanningProviderUnavailableError,
)
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.planning.ports import (
    PlanningProviderRequest,
    PlanningProviderResponse,
)
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder

Sleeper = Callable[[float], Awaitable[None]]


class OpenAIPlanningProvider:
    """Single real provider; external response details never cross this adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_builder: PlanningPromptBuilder,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30,
        max_transport_attempts: int = 2,
        retry_base_delay_seconds: float = 0.25,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
        client: httpx.AsyncClient | None = None,
        sleeper: Sleeper = asyncio.sleep,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip():
            raise PlanningProviderConfigurationError("planning provider credential is missing")
        if not model.strip():
            raise PlanningProviderConfigurationError("planning model is missing")
        if not 1 <= max_transport_attempts <= 5:
            raise PlanningProviderConfigurationError(
                "planning transport attempts must be between 1 and 5"
            )
        if timeout_seconds <= 0 or retry_base_delay_seconds <= 0:
            raise PlanningProviderConfigurationError(
                "planning timeout and retry delay must be positive"
            )
        if max_output_tokens < 1 or not 0 <= temperature <= 2:
            raise PlanningProviderConfigurationError(
                "planning output token or temperature setting is invalid"
            )
        parsed_url = httpx.URL(base_url)
        if parsed_url.scheme != "https" or parsed_url.userinfo:
            raise PlanningProviderConfigurationError(
                "planning base URL must be HTTPS and contain no credentials"
            )
        self._model = model
        self._prompt_builder = prompt_builder
        self._attempts = max_transport_attempts
        self._retry_delay = retry_base_delay_seconds
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._sleeper = sleeper
        self._monotonic = monotonic_clock
        self._client = client or httpx.AsyncClient(
            base_url=str(parsed_url).rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def generate_plan(
        self,
        request: PlanningProviderRequest,
    ) -> PlanningProviderResponse:
        prompt = self._prompt_builder.build(request)
        payload = {
            "model": self._model,
            "input": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "production_plan",
                    "schema": prompt.response_schema,
                    "strict": True,
                }
            },
            "max_output_tokens": self._max_output_tokens,
            "temperature": self._temperature,
            "store": False,
        }
        started = self._monotonic()
        response = await self._post_with_transport_retry(payload)
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        return self._convert_response(response, latency_ms=latency_ms)

    async def _post_with_transport_retry(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                response = await self._client.post("responses", json=payload)
                self._raise_for_status(response)
                return response
            except asyncio.CancelledError:
                raise
            except (
                PlanningProviderTimeoutError,
                PlanningProviderRateLimitError,
                PlanningProviderUnavailableError,
            ) as exc:
                last_error = exc
            except httpx.TimeoutException as exc:
                last_error = PlanningProviderTimeoutError("planning request timed out")
                last_error.__cause__ = exc
            except httpx.RequestError as exc:
                last_error = PlanningProviderUnavailableError(
                    "planning provider connection failed"
                )
                last_error.__cause__ = exc
            if attempt < self._attempts:
                await self._sleeper(self._retry_delay * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise PlanningProviderAuthenticationError(
                "planning provider rejected authentication"
            )
        if status == 429:
            raise PlanningProviderRateLimitError("planning provider rate limit reached")
        if status in {408, 425} or status >= 500:
            raise PlanningProviderUnavailableError("planning provider is unavailable")
        raise PlanningProviderResponseError(
            f"planning provider returned unsupported status {status}"
        )

    def _convert_response(
        self,
        response: httpx.Response,
        *,
        latency_ms: float,
    ) -> PlanningProviderResponse:
        try:
            body = response.json()
        except ValueError as exc:
            raise PlanningProviderResponseError("planning provider returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise PlanningProviderResponseError("planning provider response must be an object")
        if body.get("status") != "completed":
            raise PlanningProviderResponseError("planning provider response is incomplete")
        text = self._extract_output_text(body)
        try:
            plan_payload = json.loads(text)
            plan = ProductionPlan.model_validate(plan_payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise PlanningProviderContractError(
                "planning provider output failed contract validation"
            ) from exc
        raw_usage = body.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        reported_model = self._safe_string(body.get("model"))
        return PlanningProviderResponse(
            plan=plan,
            provider="openai",
            model=reported_model or self._model,
            requested_model=self._model,
            reported_model=reported_model,
            request_id=response.headers.get("x-request-id") or self._safe_string(body.get("id")),
            input_tokens=self._safe_int(usage.get("input_tokens")),
            output_tokens=self._safe_int(usage.get("output_tokens")),
            total_tokens=self._safe_int(usage.get("total_tokens")),
            latency_ms=latency_ms,
            finish_reason=self._safe_string(body.get("status")),
            metadata={},
        )

    @staticmethod
    def _extract_output_text(body: dict[str, Any]) -> str:
        output = body.get("output")
        if not isinstance(output, list):
            raise PlanningProviderResponseError("planning response has no output items")
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    texts.append(part["text"])
        if len(texts) != 1:
            raise PlanningProviderResponseError(
                "planning response must contain exactly one output_text"
            )
        return texts[0]

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        return value if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _safe_string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    async def close(self) -> None:
        await self._client.aclose()
