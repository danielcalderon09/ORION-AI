"""Offline bounded narration-compression provider regressions."""

from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.scripting.exceptions import (
    ScriptingProviderContractError,
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
        "id": "compression-generation",
        "model": "google/gemini-2.5-flash-lite",
        "choices": [
            {"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "cost": 0.00066,
        },
    }


def _provider(handler):
    store = InMemoryOpenRouterScriptingRequestStore()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    return (
        OpenRouterScriptingProvider(
            api_key="fake-compression-key",
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
            max_requests_per_job=2,
            sleeper=asyncio.sleep,
            monotonic_clock=lambda: 1.0,
        ),
        store,
    )


def _request_25_seconds(scripting_request):
    scenes = tuple(
        scene.model_copy(update={"estimated_duration_seconds": 12.5})
        for scene in scripting_request.plan.scenes
    )
    plan = scripting_request.plan.model_copy(
        update={"target_duration_seconds": 25, "scenes": scenes}
    )
    return scripting_request.model_copy(
        update={"plan": plan, "target_duration_seconds": 25}
    )


async def _initial_script(request) -> dict[str, object]:
    response = await SimulatedScriptingProvider().generate_script(request)
    payload = response.script.model_dump(mode="json")
    scenes = payload["scenes"]
    assert isinstance(scenes, list)
    for index, scene in enumerate(scenes):
        assert isinstance(scene, dict)
        scene["narration"] = " ".join("palabra" for _ in range(53)) + (
            "!" * (6 + index)
        )
    return payload


def _compression(
    script: dict[str, object],
    narrations: tuple[str, ...],
) -> dict[str, object]:
    scenes = script["scenes"]
    assert isinstance(scenes, list)
    return {
        "schema_version": "1.0.0",
        "scenes": [
            {
                "source_scene_number": scene["source_scene_number"],
                "narration": narration,
            }
            for scene, narration in zip(scenes, narrations, strict=True)
        ],
    }


def _without_narration(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    scenes = result["scenes"]
    assert isinstance(scenes, list)
    for scene in scenes:
        assert isinstance(scene, dict)
        scene.pop("narration")
    return result


@pytest.mark.asyncio
async def test_real_shaped_compression_uses_two_metered_requests_and_preserves_script(
    scripting_request,
) -> None:
    request = _request_25_seconds(scripting_request)
    initial = await _initial_script(request)
    compressed = _compression(
        initial,
        (
            " ".join("palabra" for _ in range(26)) + ("!" * 6),
            " ".join("palabra" for _ in range(25)) + ("!" * 7),
        ),
    )
    calls = 0
    compression_payload: dict[str, object] = {}

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls, compression_payload
        calls += 1
        if calls == 2:
            compression_payload = json.loads(http_request.content)
        payload = initial if calls == 1 else compressed
        return httpx.Response(200, json=_envelope(payload), request=http_request)

    provider, store = _provider(handler)
    try:
        result = await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    assert _without_narration(result.script.model_dump(mode="json")) == _without_narration(
        initial
    )
    user_payload = json.loads(compression_payload["messages"][1]["content"])
    assert user_payload["source_word_count"] == 106
    assert user_payload["source_punctuation_count"] == 13
    assert user_payload["source_estimated_duration_ms"] == 43_960
    assert user_payload["maximum_total_words"] == 56
    assert [scene["maximum_words"] for scene in user_payload["scenes"]] == [27, 29]
    assert "MUST NOT exceed 56 total words" in compression_payload["messages"][0][
        "content"
    ] + compression_payload["messages"][1]["content"]
    records = tuple(store.records.values())
    assert len(records) == 2
    assert {record.metadata["request_purpose"] for record in records} == {
        "production_script",
        "narration_compression",
    }
    assert len({record.request_fingerprint for record in records}) == 2
    assert sum(record.reported_cost_usd or Decimal(0) for record in records) == Decimal(
        "0.00132"
    )
    completed = store.records[(request.job_id, 2)]
    assert completed.metadata["compression_word_budget"] == 56
    assert completed.metadata["compressed_word_count"] == 51
    assert completed.metadata["compressed_estimated_duration_ms"] == 21_960


def _invalid_compression(
    kind: str,
    initial: dict[str, object],
) -> dict[str, object]:
    valid = _compression(initial, ("short scene one", "short scene two"))
    scenes = valid["scenes"]
    assert isinstance(scenes, list)
    if kind == "missing_scene":
        valid["scenes"] = scenes[:1]
    elif kind == "duplicate_scene":
        scenes[1]["source_scene_number"] = scenes[0]["source_scene_number"]
    elif kind == "unknown_scene":
        scenes[1]["source_scene_number"] = 50
    elif kind == "empty_narration":
        scenes[0]["narration"] = ""
    elif kind == "invalid_schema":
        valid["unexpected"] = True
    elif kind == "wrong_scene_count":
        valid["scenes"] = []
    else:
        raise AssertionError(kind)
    return valid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    (
        "missing_scene",
        "duplicate_scene",
        "unknown_scene",
        "empty_narration",
        "invalid_schema",
        "wrong_scene_count",
    ),
)
async def test_compression_contract_failures_stop_after_second_request(
    scripting_request,
    kind: str,
) -> None:
    request = _request_25_seconds(scripting_request)
    initial = await _initial_script(request)
    invalid = _invalid_compression(kind, initial)
    calls = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = initial if calls == 1 else invalid
        return httpx.Response(200, json=_envelope(payload), request=http_request)

    provider, store = _provider(handler)
    try:
        with pytest.raises(ScriptingProviderContractError):
            await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    assert len(store.records) == 2
    assert store.records[(request.job_id, 2)].script is None


@pytest.mark.asyncio
async def test_duration_compliant_script_skips_compression(scripting_request) -> None:
    valid = (
        await SimulatedScriptingProvider().generate_script(scripting_request)
    ).script.model_dump(mode="json")
    calls = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope(valid), request=http_request)

    provider, store = _provider(handler)
    try:
        await provider.generate_script(scripting_request)
    finally:
        await provider.close()

    assert calls == 1
    assert len(store.records) == 1
    assert next(iter(store.records.values())).metadata["request_purpose"] == "production_script"
