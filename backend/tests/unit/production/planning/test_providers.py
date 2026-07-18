"""Contract and fake-transport tests for both planning providers."""

import asyncio
import json

import httpx
import pytest

from backend.src.production.planning.exceptions import (
    PlanningProviderAuthenticationError,
    PlanningProviderContractError,
    PlanningProviderRateLimitError,
    PlanningProviderTimeoutError,
    PlanningProviderUnavailableError,
)
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.planning.providers.openai_provider import (
    OpenAIPlanningProvider,
)


def valid_plan_payload() -> dict:
    return {
        "schema_version": "1.0.0",
        "title": "Eclipse",
        "summary": "A concise eclipse explanation",
        "language": "en",
        "target_duration_seconds": 40,
        "aspect_ratio": "9:16",
        "visual_style": "cinematic",
        "narrative_style": "educational",
        "scenes": [
            {
                "scene_number": index,
                "title": f"Scene {index}",
                "narration": "Narration",
                "visual_description": "Visual",
                "image_prompt": "Safe image prompt",
                "motion_instruction": "Slow zoom",
                "estimated_duration_seconds": 10,
                "transition": "cut",
                "on_screen_text": None,
                "metadata": {},
            }
            for index in range(1, 5)
        ],
        "metadata": {},
    }


def response_body(plan: dict | str | None = None) -> dict:
    text = json.dumps(plan or valid_plan_payload()) if not isinstance(plan, str) else plan
    return {
        "id": "resp_safe",
        "model": "gpt-test",
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": text}]}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


def real_provider(handler, *, attempts: int = 1, sleeper=None):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com/v1",
    )
    provider = OpenAIPlanningProvider(
        api_key="test-key-not-real",
        model="gpt-test",
        prompt_builder=PlanningPromptBuilder(),
        client=client,
        max_transport_attempts=attempts,
        sleeper=sleeper or asyncio.sleep,
        monotonic_clock=lambda: 1.0,
    )
    return provider, client


@pytest.mark.asyncio
async def test_simulated_provider_contract_is_stable(planning_request) -> None:
    provider = SimulatedPlanningProvider()
    snapshot = planning_request.model_dump_json()
    first = await provider.generate_plan(planning_request)
    second = await provider.generate_plan(planning_request)
    assert first == second
    assert first.plan.language == planning_request.language
    assert first.plan.aspect_ratio == planning_request.aspect_ratio
    assert [scene.scene_number for scene in first.plan.scenes] == [1, 2, 3, 4]
    assert first.provider and first.model
    assert planning_request.model_dump_json() == snapshot


@pytest.mark.asyncio
async def test_real_provider_sends_structured_request_and_converts_usage(
    planning_request,
) -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=response_body(),
            headers={"x-request-id": "request-safe"},
        )

    provider, client = real_provider(handler)
    response = await provider.generate_plan(planning_request)
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["store"] is False
    assert response.total_tokens == 30
    assert response.request_id == "request-safe"
    await provider.close()
    assert client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, PlanningProviderAuthenticationError),
        (429, PlanningProviderRateLimitError),
        (503, PlanningProviderUnavailableError),
    ],
)
async def test_real_provider_translates_http_failures(
    planning_request, status, error_type
) -> None:
    provider, _ = real_provider(lambda request: httpx.Response(status, request=request))
    with pytest.raises(error_type, match="planning provider") as captured:
        await provider.generate_plan(planning_request)
    assert "test-key-not-real" not in str(captured.value)
    await provider.close()


@pytest.mark.asyncio
async def test_transport_retries_are_limited(planning_request) -> None:
    calls = 0
    delays = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429 if calls == 1 else 200, json=response_body(), request=request)

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    provider, _ = real_provider(handler, attempts=2, sleeper=sleeper)
    assert (await provider.generate_plan(planning_request)).plan.title == "Eclipse"
    assert calls == 2
    assert delays == [0.25]
    await provider.close()


@pytest.mark.asyncio
async def test_timeout_and_cancelled_error_are_not_hidden(planning_request) -> None:
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    provider, _ = real_provider(timeout)
    with pytest.raises(PlanningProviderTimeoutError):
        await provider.generate_plan(planning_request)
    await provider.close()

    async def connection_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection", request=request)

    provider, _ = real_provider(connection_error)
    with pytest.raises(PlanningProviderUnavailableError):
        await provider.generate_plan(planning_request)
    await provider.close()

    async def cancelled(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    provider, _ = real_provider(cancelled)
    with pytest.raises(asyncio.CancelledError):
        await provider.generate_plan(planning_request)
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["not-json", {"title": "incomplete"}])
async def test_invalid_provider_contract_is_rejected(planning_request, payload) -> None:
    provider, _ = real_provider(
        lambda request: httpx.Response(200, json=response_body(payload), request=request)
    )
    with pytest.raises(PlanningProviderContractError):
        await provider.generate_plan(planning_request)
    await provider.close()
