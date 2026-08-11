"""Offline duration-policy retry tests for OpenRouter scripting."""

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.scripting.exceptions import (
    ScriptingProviderDurationPolicyBudgetError,
    ScriptingProviderDurationPolicyExhaustedError,
)
from backend.src.production.scripting.openrouter_billable_gate import (
    OpenRouterScriptingBillablePolicy,
)
from backend.src.production.scripting.openrouter_request_store import (
    InMemoryOpenRouterScriptingRequestStore,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)


def _envelope(payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": "duration-retry-generation",
        "model": "google/gemini-2.5-flash-lite",
        "choices": [
            {
                "message": {"content": json.dumps(payload)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "cost": 0.00066,
        },
    }


async def _valid_script(request) -> dict[str, object]:
    response = await SimulatedScriptingProvider().generate_script(request)
    return response.script.model_dump(mode="json")


def _overlong_script(script: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(script)
    scenes = result["scenes"]
    assert isinstance(scenes, list)
    for scene in scenes:
        assert isinstance(scene, dict)
        scene["narration"] = " ".join("palabra" for _ in range(50))
    return result


def _provider(handler, *, max_requests_per_job: int = 2):
    store = InMemoryOpenRouterScriptingRequestStore()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    provider = OpenRouterScriptingProvider(
        api_key="fake-duration-retry-key",
        model="google/gemini-2.5-flash-lite",
        prompt_builder=ScriptingPromptBuilder(max_plan_bytes=100_000),
        request_store=store,
        billable_policy=OpenRouterScriptingBillablePolicy(
            allow_billable_requests=True,
            estimated_cost_usd=Decimal("0.000660"),
            maximum_authorized_cost_usd=Decimal("0.001000"),
        ),
        client=client,
        owns_client=True,
        max_transport_attempts=1,
        max_requests_per_job=max_requests_per_job,
        sleeper=asyncio.sleep,
        monotonic_clock=lambda: 1.0,
    )
    return provider, store


@pytest.mark.asyncio
async def test_duration_policy_retry_accepts_second_output_and_preserves_arc(
    scripting_request,
) -> None:
    valid = await _valid_script(scripting_request)
    responses = [_overlong_script(valid), valid]
    calls = 0
    retry_prompt = ""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls, retry_prompt
        calls += 1
        if calls == 2:
            retry_prompt = request.content.decode("utf-8")
        return httpx.Response(200, json=_envelope(responses[calls - 1]), request=request)

    provider, store = _provider(handler)
    try:
        result = await provider.generate_script(scripting_request)
    finally:
        await provider.close()

    assert calls == 2
    assert result.script.narrative_arc is not None
    assert all(scene.story_beat is not None for scene in result.script.scenes)
    assert "previous_output_exceeded_budget" in retry_prompt
    assert "narrative_context" in retry_prompt
    assert "story_beats" in retry_prompt
    assert len(store.records) == 2
    assert store.records[(scripting_request.job_id, 1)].status.value == "failed"
    assert store.records[(scripting_request.job_id, 2)].status.value == "completed"


@pytest.mark.asyncio
async def test_second_duration_policy_failure_is_exhausted_without_third_request(
    scripting_request,
) -> None:
    valid = await _valid_script(scripting_request)
    overlong = _overlong_script(valid)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope(overlong), request=request)

    provider, store = _provider(handler)
    try:
        with pytest.raises(ScriptingProviderDurationPolicyExhaustedError):
            await provider.generate_script(scripting_request)
    finally:
        await provider.close()

    assert calls == 2
    assert len(store.records) == 2
    assert all(record.status.value == "failed" for record in store.records.values())


@pytest.mark.asyncio
async def test_duration_policy_retry_budget_blocks_second_provider_call(
    scripting_request,
) -> None:
    valid = await _valid_script(scripting_request)
    overlong = _overlong_script(valid)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope(overlong), request=request)

    provider, _ = _provider(handler, max_requests_per_job=1)
    try:
        with pytest.raises(ScriptingProviderDurationPolicyBudgetError):
            await provider.generate_script(scripting_request)
    finally:
        await provider.close()

    assert calls == 1
