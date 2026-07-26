"""OpenRouter scene-planning provider contract tests without network."""

import asyncio
import json

import httpx
import pytest

from backend.src.production.scene_planning.exceptions import (
    ScenePlanningProviderAuthenticationException,
    ScenePlanningProviderContractException,
    ScenePlanningProviderRateLimitException,
    ScenePlanningProviderResponseException,
    ScenePlanningProviderUnavailableException,
)
from backend.src.production.scene_planning.prompt_builder import ScenePlanningPromptBuilder
from backend.src.production.scene_planning.providers import SimulatedScenePlanningProvider
from backend.src.production.scene_planning.providers.openrouter_provider import (
    OpenRouterScenePlanningProvider,
    RealScenePlanningProvider,
)


async def valid_plan_payload(script):
    response = await SimulatedScenePlanningProvider().generate_scene_plan(script)
    return response.scene_plan.model_dump(mode="json")


def provider(handler, *, attempts=1, sleeper=asyncio.sleep, **overrides):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    values = {
        "api_key": "fake-scene-planning-key",
        "model": "anthropic/scene-model",
        "prompt_builder": ScenePlanningPromptBuilder(max_script_bytes=100_000),
        "client": client,
        "owns_client": True,
        "max_transport_attempts": attempts,
        "sleeper": sleeper,
        "monotonic_clock": lambda: 1.0,
    }
    values.update(overrides)
    return OpenRouterScenePlanningProvider(**values), client


@pytest.mark.asyncio
async def test_openrouter_request_schema_telemetry_and_model_mismatch(
    production_script,
) -> None:
    captured = {}
    plan = await valid_plan_payload(production_script)

    async def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "request-scene-safe",
                "model": "google/reported-scene-model",
                "choices": [{"message": {"content": json.dumps(plan)}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
            request=request,
        )

    real, client = provider(handler)
    response = await real.generate_scene_plan(production_script)
    assert captured["response_format"]["json_schema"]["name"] == "production_scene_plan"
    assert captured["provider"] == {
        "require_parameters": True,
        "data_collection": "deny",
    }
    assert captured["model"] == "anthropic/scene-model"
    assert captured["store"] is False
    assert response.requested_model == "anthropic/scene-model"
    assert response.reported_model == "google/reported-scene-model"
    assert response.total_tokens == 30
    assert response.request_id == "request-scene-safe"
    await real.close()
    assert client.is_closed


@pytest.mark.asyncio
async def test_real_provider_name_and_missing_optional_telemetry(production_script) -> None:
    plan = await valid_plan_payload(production_script)

    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(plan)}}]},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    real = RealScenePlanningProvider(
        api_key="fake",
        model="qwen/scene-model",
        prompt_builder=ScenePlanningPromptBuilder(max_script_bytes=100_000),
        client=client,
    )
    response = await real.generate_scene_plan(production_script)
    assert response.reported_model is None
    assert response.total_tokens is None
    assert response.request_id is None
    await real.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ScenePlanningProviderAuthenticationException),
        (403, ScenePlanningProviderAuthenticationException),
        (429, ScenePlanningProviderRateLimitException),
        (500, ScenePlanningProviderUnavailableException),
        (502, ScenePlanningProviderUnavailableException),
        (503, ScenePlanningProviderUnavailableException),
        (400, ScenePlanningProviderResponseException),
    ],
)
async def test_openrouter_maps_safe_http_errors(production_script, status, error_type) -> None:
    real, _ = provider(lambda request: httpx.Response(status, request=request))
    with pytest.raises(error_type) as captured:
        await real.generate_scene_plan(production_script)
    assert "fake-scene-planning-key" not in str(captured.value)
    await real.close()


@pytest.mark.asyncio
async def test_openrouter_retries_are_bounded(production_script) -> None:
    calls = 0
    delays = []

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    async def sleeper(delay):
        delays.append(delay)

    real, _ = provider(handler, attempts=3, sleeper=sleeper)
    with pytest.raises(ScenePlanningProviderUnavailableException):
        await real.generate_scene_plan(production_script)
    assert calls == 3
    assert delays == [0.25, 0.5]
    await real.close()


@pytest.mark.asyncio
async def test_openrouter_rejects_invalid_json_contract_and_mapping(production_script) -> None:
    invalid_json, client = provider(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{"}}]},
            request=request,
        )
    )
    with pytest.raises(ScenePlanningProviderContractException):
        await invalid_json.generate_scene_plan(production_script)
    await invalid_json.close()
    plan = await valid_plan_payload(production_script)
    plan["scenes"][0]["narration"] = "Changed narration"
    invalid_mapping, _ = provider(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(plan)}}]},
            request=request,
        )
    )
    with pytest.raises(ScenePlanningProviderContractException):
        await invalid_mapping.generate_scene_plan(production_script)
    await invalid_mapping.close()
    assert client.is_closed


@pytest.mark.asyncio
async def test_openrouter_preserves_cancelled_error(production_script) -> None:
    async def cancel(request):
        raise asyncio.CancelledError

    real, _ = provider(cancel)
    with pytest.raises(asyncio.CancelledError):
        await real.generate_scene_plan(production_script)
    await real.close()
