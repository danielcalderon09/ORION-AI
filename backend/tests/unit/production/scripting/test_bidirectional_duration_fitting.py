"""Offline regressions for proportional occupancy and bounded expansion."""

from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.domain.duration_resolution import (
    NarrationDurationCalibration,
    NarrationDurationStatus,
    NarrationOccupancyPolicy,
)
from backend.src.production.scripting.duration_policy import (
    allocate_narration_scene_word_budgets,
    assess_narration_duration,
    narration_expansion_word_budget,
)
from backend.src.production.scripting.exceptions import (
    ScriptingProviderDurationPolicyExhaustedError,
)
from backend.src.production.scripting.narration_expansion import (
    NarrationExpansionResponse,
    merge_narration_expansion,
    narration_expansion_request,
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


@pytest.mark.parametrize("target_ms", (15_000, 25_000, 30_000, 50_000))
def test_occupancy_policy_scales_for_supported_durations(target_ms: int) -> None:
    policy = NarrationOccupancyPolicy()

    short = policy.assess(
        narration_duration_ms=target_ms * 7 // 10,
        target_duration_ms=target_ms,
    )
    acceptable = policy.assess(
        narration_duration_ms=target_ms * 94 // 100,
        target_duration_ms=target_ms,
    )
    long = policy.assess(
        narration_duration_ms=target_ms + 1,
        target_duration_ms=target_ms,
    )

    assert short.status is NarrationDurationStatus.TOO_SHORT
    assert acceptable.status is NarrationDurationStatus.ACCEPTABLE
    assert long.status is NarrationDurationStatus.TOO_LONG


def test_estimator_calibration_is_scoped_and_neutral_by_default() -> None:
    policy = NarrationOccupancyPolicy()
    neutral = policy.assess(
        narration_duration_ms=19_520,
        target_duration_ms=25_000,
    )
    calibrated = policy.assess(
        narration_duration_ms=19_520,
        target_duration_ms=25_000,
        calibration=NarrationDurationCalibration(
            provider="openrouter",
            model="kokoro",
            language="es-ES",
            duration_ratio=Decimal("0.905"),
            measurement_count=1,
        ),
    )

    assert neutral.calibration_ratio == Decimal("1")
    assert calibrated.narration_duration_ms == 17_665
    assert calibrated.status is NarrationDurationStatus.TOO_SHORT


def _envelope(payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": "expansion-generation",
        "model": "google/gemini-2.5-flash-lite",
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
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
            api_key="fake-expansion-key",
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


def _without_narration(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    scenes = result["scenes"]
    assert isinstance(scenes, list)
    for scene in scenes:
        assert isinstance(scene, dict)
        scene.pop("narration")
    return result


@pytest.mark.asyncio
async def test_short_script_uses_one_narration_only_expansion(scripting_request) -> None:
    initial_script = (
        await SimulatedScriptingProvider().generate_script(scripting_request)
    ).script.model_dump(mode="json")
    initial_scenes = initial_script["scenes"]
    assert isinstance(initial_scenes, list)
    for index, scene in enumerate(initial_scenes, start=1):
        assert isinstance(scene, dict)
        scene["narration"] = " ".join(f"short{index}" for _ in range(10)) + "."
    expanded = {
        "schema_version": "1.0.0",
        "language": "en",
        "scenes": [
            {
                "source_scene_number": index,
                "narration": " ".join(f"useful{index}" for _ in range(words)) + ".",
            }
            for index, words in ((1, 21), (2, 23))
        ],
    }
    responses = [initial_script, expanded]
    calls = 0
    second_payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls, second_payload
        calls += 1
        if calls == 2:
            second_payload = json.loads(request.content)
        return httpx.Response(200, json=_envelope(responses[calls - 1]), request=request)

    provider, store = _provider(handler)
    try:
        result = await provider.generate_script(scripting_request)
    finally:
        await provider.close()

    assert calls == 2
    assert _without_narration(result.script.model_dump(mode="json")) == _without_narration(
        initial_script
    )
    assert '"name":"narration_expansion"' in json.dumps(second_payload, separators=(",", ":"))
    prompt = second_payload["messages"][1]["content"]
    assert isinstance(prompt, str)
    prompt_payload = json.loads(prompt)
    assert prompt_payload["source_script_sha256"]
    assert prompt_payload["maximum_total_words"] == 44
    assert [scene["maximum_words"] for scene in prompt_payload["scenes"]] == [21, 23]
    assert len(store.records) == 2
    assert store.records[(scripting_request.job_id, 2)].metadata["request_purpose"] == (
        "narration_expansion"
    )
    assert sum(record.reported_cost_usd or Decimal(0) for record in store.records.values()) == (
        Decimal("0.00132")
    )


@pytest.mark.asyncio
async def test_short_expansion_still_short_fails_without_third_request(
    scripting_request,
) -> None:
    initial = (
        await SimulatedScriptingProvider().generate_script(scripting_request)
    ).script.model_dump(mode="json")
    scenes = initial["scenes"]
    assert isinstance(scenes, list)
    for scene in scenes:
        assert isinstance(scene, dict)
        scene["narration"] = "brief facts."
    still_short = {
        "schema_version": "1.0.0",
        "language": "en",
        "scenes": [
            {"source_scene_number": index, "narration": "still brief facts."}
            for index in (1, 2)
        ],
    }
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_envelope(initial if calls == 1 else still_short),
            request=request,
        )

    provider, _ = _provider(handler)
    try:
        with pytest.raises(ScriptingProviderDurationPolicyExhaustedError):
            await provider.generate_script(scripting_request)
    finally:
        await provider.close()

    assert calls == 2


@pytest.mark.asyncio
async def test_expansion_source_hash_and_language_are_pinned(scripting_request) -> None:
    source = (await SimulatedScriptingProvider().generate_script(scripting_request)).script
    assessment = assess_narration_duration(
        narrations=tuple(scene.narration for scene in source.scenes),
        target_duration_seconds=source.target_duration_seconds,
        reading_speed_words_per_minute=150,
    )
    budget = narration_expansion_word_budget(assessment, scene_count=2)
    request = narration_expansion_request(
        job_id=scripting_request.job_id,
        source_script=source,
        assessment=assessment,
        minimum_duration_ms=17_600,
        ideal_duration_ms=18_800,
        maximum_total_words=budget,
        scene_word_budgets=allocate_narration_scene_word_budgets(
            scene_count=2,
            maximum_total_words=budget,
        ),
    )
    wrong_language = NarrationExpansionResponse(
        language="es",
        scenes=(
            {"source_scene_number": 1, "narration": "Narración útil suficiente."},
            {"source_scene_number": 2, "narration": "Narración final suficiente."},
        ),
    )

    with pytest.raises(ValueError, match="language"):
        merge_narration_expansion(
            source_script=source,
            request=request,
            response=wrong_language,
        )
    changed = source.model_copy(update={"title": "Changed source"})
    with pytest.raises(ValueError, match="hash"):
        merge_narration_expansion(
            source_script=changed,
            request=request,
            response=wrong_language.model_copy(update={"language": "en"}),
        )


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
async def test_expansion_contract_failures_are_rejected(
    scripting_request,
    kind: str,
) -> None:
    source = (await SimulatedScriptingProvider().generate_script(scripting_request)).script
    assessment = assess_narration_duration(
        narrations=tuple(scene.narration for scene in source.scenes),
        target_duration_seconds=source.target_duration_seconds,
        reading_speed_words_per_minute=150,
    )
    budget = narration_expansion_word_budget(assessment, scene_count=2)
    request = narration_expansion_request(
        job_id=scripting_request.job_id,
        source_script=source,
        assessment=assessment,
        minimum_duration_ms=17_600,
        ideal_duration_ms=18_800,
        maximum_total_words=budget,
        scene_word_budgets=allocate_narration_scene_word_budgets(
            scene_count=2,
            maximum_total_words=budget,
        ),
    )
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "language": "en",
        "scenes": [
            {"source_scene_number": 1, "narration": "Useful scene one."},
            {"source_scene_number": 2, "narration": "Useful scene two."},
        ],
    }
    scenes = payload["scenes"]
    assert isinstance(scenes, list)
    if kind in {"missing_scene", "wrong_scene_count"}:
        payload["scenes"] = scenes[:1] if kind == "missing_scene" else []
    elif kind == "duplicate_scene":
        scenes[1]["source_scene_number"] = 1
    elif kind == "unknown_scene":
        scenes[1]["source_scene_number"] = 50
    elif kind == "empty_narration":
        scenes[0]["narration"] = ""
    elif kind == "invalid_schema":
        payload["unexpected"] = True

    with pytest.raises((ValueError, TypeError)):
        response = NarrationExpansionResponse.model_validate(payload)
        merge_narration_expansion(
            source_script=source,
            request=request,
            response=response,
        )
