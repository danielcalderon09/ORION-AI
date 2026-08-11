import json
from uuid import UUID

import httpx
import pytest

from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingProviderError,
    NarrationFittingRequest,
    NarrationFittingTransientProviderError,
)
from backend.src.production.speech_generation.providers.openrouter_narration_fitter import (
    OpenRouterNarrationFittingProvider,
)


def _request() -> NarrationFittingRequest:
    return NarrationFittingRequest(
        job_id=UUID("10000000-0000-4000-8000-000000009001"),
        scene_id="scene-001",
        sequence_index=0,
        attempt_number=1,
        current_narration=(
            "En las profundidades del océano, criaturas antiguas emiten luces "
            "misteriosas para sobrevivir sin el sol."
        ),
        current_duration_ms=5_325,
        target_duration_ms=4_000,
        language="es-ES",
        tone="misterioso y documental",
    )


def _provider(transport: httpx.MockTransport) -> OpenRouterNarrationFittingProvider:
    return OpenRouterNarrationFittingProvider(
        api_key="test-key-not-real",
        model="google/gemini-2.5-flash-lite",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=5,
        max_output_tokens=256,
        temperature=0.1,
        max_response_bytes=100_000,
        client=httpx.AsyncClient(transport=transport),
    )


async def test_openrouter_fitter_uses_one_bounded_structured_request() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path == "/api/v1/chat/completions"
        assert payload["model"] == "google/gemini-2.5-flash-lite"
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            headers={"x-request-id": "fit-request-1"},
            json={
                "id": "generation-1",
                "model": "google/gemini-2.5-flash-lite",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"narration": "Criaturas antiguas brillan bajo el océano."}
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 12,
                    "total_tokens": 92,
                    "cost": "0.0001",
                },
            },
        )

    provider = _provider(httpx.MockTransport(respond))
    try:
        result = await provider.revise(_request())
    finally:
        await provider.close()

    assert len(requests) == 1
    assert result.revised_narration == "Criaturas antiguas brillan bajo el océano."
    assert result.provider_request_id == "fit-request-1"
    assert str(result.reported_cost_usd) == "0.0001"


async def test_openrouter_fitter_timeout_retries_once_then_succeeds() -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("fake timeout", request=request)
        return httpx.Response(
            200,
            headers={"x-request-id": "fit-request-retry"},
            json={
                "model": "google/gemini-2.5-flash-lite",
                "choices": [{"message": {"content": json.dumps(
                    {"narration": "Criaturas antiguas brillan bajo el ocÃ©ano."}
                )}}],
            },
            request=request,
        )

    provider = _provider(httpx.MockTransport(timeout))
    try:
        result = await provider.revise(_request())
    finally:
        await provider.close()

    assert calls == 2
    assert result.provider_retry_count == 1


@pytest.mark.asyncio
async def test_openrouter_fitter_http_500_retries_once() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, headers={"x-request-id": "server-1"}, request=request)
        return httpx.Response(
            200,
            json={
                "model": "google/gemini-2.5-flash-lite",
                "choices": [{"message": {"content": json.dumps(
                    {"narration": "Criaturas antiguas brillan bajo el ocÃ©ano."}
                )}}],
            },
            request=request,
        )

    provider = _provider(httpx.MockTransport(respond))
    try:
        result = await provider.revise(_request())
    finally:
        await provider.close()
    assert calls == 2
    assert result.provider_retry_count == 1


@pytest.mark.asyncio
async def test_openrouter_fitter_http_429_retries_once() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"x-request-id": "rate-1"}, request=request)
        return httpx.Response(
            200,
            json={
                "model": "google/gemini-2.5-flash-lite",
                "choices": [{"message": {"content": json.dumps(
                    {"narration": "Criaturas antiguas brillan bajo el ocÃ©ano."}
                )}}],
            },
            request=request,
        )

    provider = _provider(httpx.MockTransport(respond))
    try:
        result = await provider.revise(_request())
    finally:
        await provider.close()
    assert calls == 2
    assert result.provider_retry_count == 1


@pytest.mark.asyncio
async def test_openrouter_fitter_http_400_is_not_retried_and_is_diagnostic() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, headers={"x-request-id": "bad-1"}, request=request)

    provider = _provider(httpx.MockTransport(respond))
    try:
        with pytest.raises(NarrationFittingProviderError) as error:
            await provider.revise(_request())
    finally:
        await provider.close()
    assert calls == 1
    assert error.value.safe_error_code == "http_400"
    assert error.value.retryable is False
    assert error.value.http_status == 400
    assert error.value.provider_request_id == "bad-1"


@pytest.mark.asyncio
async def test_openrouter_fitter_invalid_json_is_not_retried() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"x-request-id": "json-1"},
            content=b'{"choices":[{"message":{"content":"not json"}}]}',
            request=request,
        )

    provider = _provider(httpx.MockTransport(respond))
    try:
        with pytest.raises(NarrationFittingProviderError) as error:
            await provider.revise(_request())
    finally:
        await provider.close()
    assert calls == 1
    assert error.value.safe_error_code == "invalid_json"
    assert error.value.response_received is True


@pytest.mark.asyncio
async def test_openrouter_fitter_contract_failure_is_not_retried() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"wrong": "field"})}}]},
            request=request,
        )

    provider = _provider(httpx.MockTransport(respond))
    try:
        with pytest.raises(NarrationFittingProviderError) as error:
            await provider.revise(_request())
    finally:
        await provider.close()
    assert calls == 1
    assert error.value.safe_error_code == "contract_error"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_openrouter_fitter_retry_budget_blocks_second_call() -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fake timeout", request=request)

    provider = _provider(httpx.MockTransport(timeout))
    try:
        with pytest.raises(NarrationFittingTransientProviderError) as error:
            await provider.revise(_request().model_copy(update={"maximum_provider_retries": 0}))
    finally:
        await provider.close()
    assert calls == 1
    assert error.value.safe_error_code == "timeout"
    assert error.value.retryable is True
