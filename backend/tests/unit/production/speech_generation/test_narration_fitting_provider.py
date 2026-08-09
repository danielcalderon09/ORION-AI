import json
from uuid import UUID

import httpx
import pytest

from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingRequest,
    NarrationFittingUncertainError,
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


async def test_openrouter_fitter_timeout_is_uncertain_without_retry() -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fake timeout", request=request)

    provider = _provider(httpx.MockTransport(timeout))
    try:
        with pytest.raises(NarrationFittingUncertainError):
            await provider.revise(_request())
    finally:
        await provider.close()

    assert calls == 1
