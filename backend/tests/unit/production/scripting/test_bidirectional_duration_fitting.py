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
    ScriptingProviderContractError,
    ScriptingProviderDurationPolicyExhaustedError,
)
from backend.src.production.scripting.narration_expansion import (
    NarrationExpansionContractError,
    NarrationExpansionFailureCode,
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


def _twenty_five_second_request(scripting_request):
    scenes = tuple(
        scene.model_copy(update={"estimated_duration_seconds": 12.5})
        for scene in scripting_request.plan.scenes
    )
    return scripting_request.model_copy(
        update={
            "plan": scripting_request.plan.model_copy(
                update={"target_duration_seconds": 25, "scenes": scenes}
            ),
            "target_duration_seconds": 25,
        }
    )


def _word_narration(prefix: str, count: int, *, punctuation: str = ".!") -> str:
    return " ".join(f"{prefix}{index}" for index in range(count)) + punctuation


async def _real_shaped_initial(scripting_request) -> dict[str, object]:
    payload = (
        await SimulatedScriptingProvider().generate_script(scripting_request)
    ).script.model_dump(mode="json")
    scenes = payload["scenes"]
    assert isinstance(scenes, list)
    scenes[0]["narration"] = _word_narration("sourceone", 22)
    scenes[1]["narration"] = _word_narration("sourcetwo", 23)
    return payload


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
    with pytest.raises(ValueError, match="source binding"):
        merge_narration_expansion(
            source_script=changed,
            request=request,
            response=wrong_language.model_copy(update={"language": "en"}),
        )


@pytest.mark.asyncio
async def test_real_shaped_45_word_expansion_succeeds_with_two_requests(
    scripting_request,
) -> None:
    request = _twenty_five_second_request(scripting_request)
    initial = await _real_shaped_initial(request)
    expanded = {
        "schema_version": "1.0.0",
        "language": "en",
        "scenes": [
            {
                "source_scene_number": 1,
                "narration": _word_narration("expandedone", 27),
            },
            {
                "source_scene_number": 2,
                "narration": _word_narration("expandedtwo", 28),
            },
        ],
    }
    responses = [initial, expanded]
    calls = 0
    second_payload: dict[str, object] = {}

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls, second_payload
        calls += 1
        if calls == 2:
            second_payload = json.loads(http_request.content)
        return httpx.Response(
            200,
            json=_envelope(responses[calls - 1]),
            request=http_request,
        )

    provider, store = _provider(handler)
    try:
        result = await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    assert len(store.records) == 2
    assert store.records[(request.job_id, 1)].metadata["estimated_duration_ms"] == 18_480
    completed = store.records[(request.job_id, 2)]
    assert completed.script is not None
    assert completed.metadata["expanded_word_count"] == 55
    assert completed.metadata["expanded_estimated_duration_ms"] == 22_480
    assert _without_narration(result.script.model_dump(mode="json")) == _without_narration(
        initial
    )
    expansion_prompt = json.loads(second_payload["messages"][1]["content"])
    assert expansion_prompt["maximum_total_words"] == 55
    assert [scene["maximum_words"] for scene in expansion_prompt["scenes"]] == [27, 28]
    response_schema = second_payload["response_format"]["json_schema"]["schema"]
    assert response_schema["properties"]["language"]["enum"] == ["en"]
    assert response_schema["properties"]["scenes"]["minItems"] == 2
    assert response_schema["properties"]["scenes"]["maxItems"] == 2
    assert response_schema["$defs"]["NarrationExpansionScene"]["properties"][
        "source_scene_number"
    ]["enum"] == [1, 2]
    assert "sourceone" not in completed.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_code"),
    (
        ("wrong_language", NarrationExpansionFailureCode.LANGUAGE_MISMATCH),
        ("missing_scene", NarrationExpansionFailureCode.SCENE_MISSING),
        ("duplicate_scene", NarrationExpansionFailureCode.SCENE_DUPLICATE),
        ("unknown_scene", NarrationExpansionFailureCode.SCENE_UNKNOWN),
        ("empty_narration", NarrationExpansionFailureCode.EMPTY_NARRATION),
        ("unsafe_narration", NarrationExpansionFailureCode.UNSAFE_NARRATION),
        ("scene_one_budget", NarrationExpansionFailureCode.SCENE_BUDGET_EXCEEDED),
        ("scene_two_budget", NarrationExpansionFailureCode.SCENE_BUDGET_EXCEEDED),
        ("invalid_schema", NarrationExpansionFailureCode.SCHEMA_INVALID),
    ),
)
async def test_expansion_failures_persist_safe_leaf_diagnostics_without_third_request(
    scripting_request,
    kind: str,
    expected_code: NarrationExpansionFailureCode,
) -> None:
    request = _twenty_five_second_request(scripting_request)
    initial = await _real_shaped_initial(request)
    expanded: dict[str, object] = {
        "schema_version": "1.0.0",
        "language": "en",
        "scenes": [
            {"source_scene_number": 1, "narration": _word_narration("safeone", 27)},
            {"source_scene_number": 2, "narration": _word_narration("safetwo", 28)},
        ],
    }
    scenes = expanded["scenes"]
    assert isinstance(scenes, list)
    if kind == "wrong_language":
        expanded["language"] = "es"
    elif kind == "missing_scene":
        expanded["scenes"] = scenes[:1]
    elif kind == "duplicate_scene":
        scenes[1]["source_scene_number"] = 1
    elif kind == "unknown_scene":
        scenes[1]["source_scene_number"] = 3
    elif kind == "empty_narration":
        scenes[0]["narration"] = " "
    elif kind == "unsafe_narration":
        scenes[0]["narration"] = "<script>RAW_NARRATION_SENTINEL</script>"
    elif kind == "scene_one_budget":
        scenes[0]["narration"] = _word_narration("RAW_NARRATION_SENTINEL", 28)
    elif kind == "scene_two_budget":
        scenes[1]["narration"] = _word_narration("RAW_NARRATION_SENTINEL", 29)
    elif kind == "invalid_schema":
        expanded["unexpected"] = "RAW_PROVIDER_SENTINEL"
    calls = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_envelope(initial if calls == 1 else expanded),
            request=http_request,
        )

    provider, store = _provider(handler)
    try:
        with pytest.raises(ScriptingProviderContractError):
            await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    failed = store.records[(request.job_id, 2)]
    assert failed.validation_error_code is not None
    assert failed.validation_error_code.value == expected_code.value
    assert failed.metadata["expected_language"] == "en"
    assert failed.metadata["expected_scene_numbers"] == "1,2"
    assert failed.metadata["expected_scene_count"] == 2
    assert failed.metadata["scene_word_budgets"] == "1:27,2:28"
    assert failed.metadata["global_word_budget"] == 55
    serialized = failed.model_dump_json()
    assert "RAW_NARRATION_SENTINEL" not in serialized
    assert "RAW_PROVIDER_SENTINEL" not in serialized


def test_global_expansion_budget_is_implied_by_hard_scene_budgets() -> None:
    budgets = allocate_narration_scene_word_budgets(
        scene_count=2,
        maximum_total_words=55,
    )

    assert budgets == (27, 28)
    assert sum(budgets) == 55


def test_dynamic_expansion_schema_is_deterministic(scripting_request) -> None:
    source = asyncio.run(SimulatedScriptingProvider().generate_script(scripting_request)).script
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
    builder = ScriptingPromptBuilder(max_plan_bytes=100_000)

    first = builder.build_expansion(request)
    second = builder.build_expansion(request)

    assert first == second
    assert first.response_schema["additionalProperties"] is False
    scene_definition = first.response_schema["$defs"]["NarrationExpansionScene"]
    assert scene_definition["additionalProperties"] is False
    assert set(first.response_schema["required"]) == {"schema_version", "language", "scenes"}


def test_source_mismatch_has_stable_safe_leaf_code(scripting_request) -> None:
    source = asyncio.run(SimulatedScriptingProvider().generate_script(scripting_request)).script
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
    response = NarrationExpansionResponse(
        language="en",
        scenes=tuple(
            {
                "source_scene_number": scene.source_scene_number,
                "narration": scene.original_narration,
            }
            for scene in request.scenes
        ),
    )

    with pytest.raises(NarrationExpansionContractError) as captured:
        merge_narration_expansion(
            source_script=source.model_copy(update={"title": "changed"}),
            request=request,
            response=response,
        )

    assert captured.value.code is NarrationExpansionFailureCode.SOURCE_MISMATCH
    assert captured.value.safe_metadata["source_script_sha256"] == request.source_script_sha256


def test_invalid_merged_script_has_stable_safe_leaf_code(scripting_request) -> None:
    source = asyncio.run(SimulatedScriptingProvider().generate_script(scripting_request)).script
    invalid_source = source.model_copy(update={"title": ""})
    assessment = assess_narration_duration(
        narrations=tuple(scene.narration for scene in invalid_source.scenes),
        target_duration_seconds=invalid_source.target_duration_seconds,
        reading_speed_words_per_minute=150,
    )
    budget = narration_expansion_word_budget(assessment, scene_count=2)
    request = narration_expansion_request(
        job_id=scripting_request.job_id,
        source_script=invalid_source,
        assessment=assessment,
        minimum_duration_ms=17_600,
        ideal_duration_ms=18_800,
        maximum_total_words=budget,
        scene_word_budgets=allocate_narration_scene_word_budgets(
            scene_count=2,
            maximum_total_words=budget,
        ),
    )
    response = NarrationExpansionResponse(
        language="en",
        scenes=tuple(
            {
                "source_scene_number": scene.source_scene_number,
                "narration": scene.original_narration,
            }
            for scene in request.scenes
        ),
    )

    with pytest.raises(NarrationExpansionContractError) as captured:
        merge_narration_expansion(
            source_script=invalid_source,
            request=request,
            response=response,
        )

    assert captured.value.code is NarrationExpansionFailureCode.MERGE_INVALID


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
