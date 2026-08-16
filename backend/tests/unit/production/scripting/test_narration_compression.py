"""Offline bounded narration-compression provider regressions."""

from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.scripting.duration_policy import (
    allocate_narration_scene_word_budgets,
    assess_narration_duration,
    narration_compression_word_band,
)
from backend.src.production.scripting.exceptions import (
    ScriptingProviderContractError,
)
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.narration_compression import (
    NarrationCompressionContractError,
    NarrationCompressionFailureCode,
    NarrationCompressionResponse,
    NarrationCompressionScene,
    merge_narration_compression,
    narration_compression_request,
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


async def _audited_initial_script(request) -> dict[str, object]:
    response = await SimulatedScriptingProvider().generate_script(request)
    payload = response.script.model_dump(mode="json")
    scenes = payload["scenes"]
    assert isinstance(scenes, list)
    for index, scene in enumerate(scenes):
        assert isinstance(scene, dict)
        scene["narration"] = " ".join("palabra" for _ in range(32)) + (
            "!" * (3 + index)
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
            " ".join("palabra" for _ in range(27)) + ("!" * 6),
            " ".join("palabra" for _ in range(28)) + ("!" * 7),
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
    assert user_payload["minimum_duration_ms"] == 22_000
    assert user_payload["ideal_duration_ms"] == 23_500
    assert user_payload["maximum_duration_ms"] == 24_500
    assert user_payload["minimum_total_words"] == 55
    assert user_payload["maximum_total_words"] == 56
    assert [scene["minimum_words"] for scene in user_payload["scenes"]] == [27, 28]
    assert [scene["maximum_words"] for scene in user_payload["scenes"]] == [27, 29]
    combined_prompt = compression_payload["messages"][0]["content"] + (
        compression_payload["messages"][1]["content"]
    )
    assert "between 55 and 56 total words" in combined_prompt
    assert "Do not make the narration as short as possible" in combined_prompt
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
    assert completed.metadata["compressed_word_count"] == 55
    assert completed.metadata["compressed_estimated_duration_ms"] == 23_560
    assert len(user_payload["source_script_sha256"]) == 64
    assert (
        user_payload["source_script_sha256"]
        == completed.fingerprint_input.source_script_sha256
    )


@pytest.mark.asyncio
async def test_audited_64_word_compression_rejects_46_word_underflow_safely(
    scripting_request,
) -> None:
    request = _request_25_seconds(scripting_request)
    initial = await _audited_initial_script(request)
    underflow = _compression(
        initial,
        (
            " ".join("palabra" for _ in range(23)) + ("!" * 3),
            " ".join("palabra" for _ in range(23)) + ("!" * 4),
        ),
    )
    calls = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = initial if calls == 1 else underflow
        return httpx.Response(200, json=_envelope(payload), request=http_request)

    provider, store = _provider(handler)
    try:
        with pytest.raises(ScriptingProviderContractError):
            await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    rejected = store.records[(request.job_id, 2)]
    assert rejected.validation_error_code is not None
    assert (
        rejected.validation_error_code.value
        == "narration_compression_below_minimum_word_budget"
    )
    assert rejected.metadata["minimum_total_words"] == 55
    assert rejected.metadata["maximum_total_words"] == 56
    assert rejected.metadata["received_total_word_count"] == 46
    assert rejected.metadata["scene_word_bands"] == "1:27-27,2:28-29"
    assert rejected.metadata["received_scene_word_counts"] == "1:23,2:23"
    assert rejected.metadata["raw_response_persisted"] is False
    assert "palabra" not in json.dumps(rejected.metadata)


@pytest.mark.asyncio
async def test_audited_64_word_compression_accepts_ideal_band(
    scripting_request,
) -> None:
    request = _request_25_seconds(scripting_request)
    initial = await _audited_initial_script(request)
    compliant = _compression(
        initial,
        (
            " ".join("palabra" for _ in range(27)) + ("!" * 3),
            " ".join("palabra" for _ in range(28)) + ("!" * 4),
        ),
    )
    calls = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = initial if calls == 1 else compliant
        return httpx.Response(200, json=_envelope(payload), request=http_request)

    provider, store = _provider(handler)
    try:
        result = await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    assert sum(len(scene.narration.split()) for scene in result.script.scenes) == 55
    completed = store.records[(request.job_id, 2)]
    assert completed.metadata["compressed_estimated_duration_ms"] == 22_840


@pytest.mark.asyncio
async def test_compression_above_global_band_fails_closed_after_two_requests(
    scripting_request,
) -> None:
    request = _request_25_seconds(scripting_request)
    initial = await _audited_initial_script(request)
    overflow = _compression(
        initial,
        (
            " ".join("palabra" for _ in range(28)),
            " ".join("palabra" for _ in range(29)),
        ),
    )
    calls = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = initial if calls == 1 else overflow
        return httpx.Response(200, json=_envelope(payload), request=http_request)

    provider, store = _provider(handler)
    try:
        with pytest.raises(ScriptingProviderContractError):
            await provider.generate_script(request)
    finally:
        await provider.close()

    assert calls == 2
    rejected = store.records[(request.job_id, 2)]
    assert rejected.validation_error_code is not None
    assert (
        rejected.validation_error_code.value
        == "narration_compression_above_maximum_word_budget"
    )
    assert rejected.metadata["received_total_word_count"] == 57


async def _compression_contract_fixture(scripting_request):
    request = _request_25_seconds(scripting_request)
    source = ProductionScript.model_validate(await _audited_initial_script(request))
    assessment = assess_narration_duration(
        narrations=tuple(scene.narration for scene in source.scenes),
        target_duration_seconds=25,
        reading_speed_words_per_minute=150,
    )
    band = narration_compression_word_band(assessment, scene_count=2)
    contract = narration_compression_request(
        job_id=request.job_id,
        source_script=source,
        assessment=assessment,
        minimum_duration_ms=band.minimum_duration_ms,
        ideal_duration_ms=band.ideal_duration_ms,
        maximum_duration_ms=band.maximum_duration_ms,
        minimum_total_words=band.minimum_total_words,
        maximum_total_words=band.maximum_total_words,
        scene_minimum_word_budgets=allocate_narration_scene_word_budgets(
            scene_count=2,
            maximum_total_words=band.minimum_total_words,
        ),
        scene_maximum_word_budgets=allocate_narration_scene_word_budgets(
            scene_count=2,
            maximum_total_words=band.maximum_total_words,
        ),
    )
    return source, contract


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counts", "expected_code"),
    (
        ((26, 29), NarrationCompressionFailureCode.SCENE_BELOW_MINIMUM),
        ((28, 28), NarrationCompressionFailureCode.SCENE_ABOVE_MAXIMUM),
    ),
)
async def test_compression_scene_bands_fail_closed_with_safe_codes(
    scripting_request,
    counts: tuple[int, int],
    expected_code: NarrationCompressionFailureCode,
) -> None:
    source, contract = await _compression_contract_fixture(scripting_request)
    response = NarrationCompressionResponse(
        scenes=tuple(
            NarrationCompressionScene(
                source_scene_number=index,
                narration=" ".join("palabra" for _ in range(count)),
            )
            for index, count in enumerate(counts, start=1)
        )
    )

    with pytest.raises(NarrationCompressionContractError) as raised:
        merge_narration_compression(
            source_script=source,
            request=contract,
            response=response,
        )

    assert raised.value.code is expected_code
    assert "palabra" not in json.dumps(raised.value.safe_metadata)


@pytest.mark.asyncio
async def test_compression_source_hash_mismatch_fails_closed(scripting_request) -> None:
    source, contract = await _compression_contract_fixture(scripting_request)
    response = NarrationCompressionResponse(
        scenes=(
            NarrationCompressionScene(
                source_scene_number=1,
                narration=" ".join("palabra" for _ in range(27)),
            ),
            NarrationCompressionScene(
                source_scene_number=2,
                narration=" ".join("palabra" for _ in range(28)),
            ),
        )
    )
    changed_source = source.model_copy(update={"title": f"{source.title} changed"})

    with pytest.raises(NarrationCompressionContractError) as raised:
        merge_narration_compression(
            source_script=changed_source,
            request=contract,
            response=response,
        )

    assert raised.value.code is NarrationCompressionFailureCode.SOURCE_MISMATCH


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
