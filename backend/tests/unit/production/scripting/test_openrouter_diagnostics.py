"""Sanitized durable diagnostics for failed OpenRouter structured output."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.scripting.exceptions import (
    ScriptingProviderContractError,
    ScriptingProviderResponseError,
)
from backend.src.production.scripting.openrouter_billable_gate import (
    OpenRouterScriptingBillablePolicy,
)
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingRequestRecord,
    OpenRouterScriptingRequestStatus,
    OpenRouterScriptingValidationErrorCode,
)
from backend.src.production.scripting.openrouter_request_store import (
    InMemoryOpenRouterScriptingRequestStore,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)

FAKE_API_KEY = "fake-diagnostics-api-key"


async def _valid_script(scripting_request) -> dict[str, object]:
    response = await SimulatedScriptingProvider().generate_script(scripting_request)
    return response.script.model_dump(mode="json")


def _envelope(content: dict[str, object] | str) -> dict[str, object]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "id": "generation-safe",
        "model": "google/reported-safe",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "total_tokens": 33,
            "cost": 0.0004,
        },
    }


def _provider(
    handler,
    *,
    store: InMemoryOpenRouterScriptingRequestStore | None = None,
) -> tuple[
    OpenRouterScriptingProvider,
    InMemoryOpenRouterScriptingRequestStore,
]:
    durable_store = store or InMemoryOpenRouterScriptingRequestStore()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    return (
        OpenRouterScriptingProvider(
            api_key=FAKE_API_KEY,
            model="google/gemini-2.5-flash-lite",
            prompt_builder=ScriptingPromptBuilder(max_plan_bytes=100_000),
            request_store=durable_store,
            billable_policy=OpenRouterScriptingBillablePolicy(
                allow_billable_requests=True,
                estimated_cost_usd=Decimal("0.0006600"),
                maximum_authorized_cost_usd=Decimal("0.001000000"),
            ),
            client=client,
            owns_client=True,
            max_transport_attempts=1,
            sleeper=asyncio.sleep,
            monotonic_clock=lambda: 1.0,
        ),
        durable_store,
    )


def _record(store: InMemoryOpenRouterScriptingRequestStore) -> OpenRouterScriptingRequestRecord:
    return next(iter(store.records.values()))


@pytest.mark.asyncio
async def test_malformed_provider_envelope_retains_safe_transport_metadata(
    scripting_request,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=b"[]",
            headers={"x-request-id": "request-envelope-safe"},
            request=request,
        )

    provider, store = _provider(handler)
    with pytest.raises(ScriptingProviderResponseError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert calls == 1
    assert record.status is OpenRouterScriptingRequestStatus.FAILED
    assert record.safe_error_code == "provider_protocol_error"
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.PROVIDER_ENVELOPE_PROTOCOL
    )
    assert record.http_status == 200
    assert record.provider_request_id == "request-envelope-safe"
    assert record.fresh_submission_permitted is False
    await provider.close()


@pytest.mark.asyncio
async def test_provider_body_error_retains_safe_metadata_but_not_raw_body_or_key(
    scripting_request,
) -> None:
    calls = 0
    response_body = _envelope("unused")
    response_body["error"] = {
        "message": "RAW_PROVIDER_SENTINEL must never be persisted",
        "debug": {"authorization": "Bearer secret"},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=response_body,
            headers={"x-request-id": "request-body-error-safe"},
            request=request,
        )

    provider, store = _provider(handler)
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.PROVIDER_BODY_ERROR
    )
    assert record.validation_error_path == "error"
    assert record.validation_error_message == "provider returned an error object"
    assert record.http_status == 200
    assert record.requested_model == "google/gemini-2.5-flash-lite"
    assert record.provider_request_id == "request-body-error-safe"
    assert record.reported_model == "google/reported-safe"
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (11, 22, 33)
    assert record.reported_cost_usd == Decimal("0.0004")
    serialized = record.model_dump_json()
    assert "RAW_PROVIDER_SENTINEL" not in serialized
    assert "Bearer secret" not in serialized
    assert FAKE_API_KEY not in serialized
    assert record.metadata == {"raw_response_persisted": False}
    assert record.script is None
    assert record.fresh_submission_permitted is False
    with pytest.raises(ScriptingProviderContractError, match="cannot be retried"):
        await provider.generate_script(scripting_request)
    assert calls == 1
    await provider.close()


@pytest.mark.asyncio
async def test_missing_content_is_output_text_protocol_with_missing_metadata_null(
    scripting_request,
) -> None:
    provider, store = _provider(
        lambda request: httpx.Response(200, json={"choices": []}, request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.OUTPUT_TEXT_PROTOCOL
    )
    assert record.validation_error_path == "choices[0].message.content"
    assert record.http_status == 200
    assert record.provider_request_id is None
    assert record.reported_model is None
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.total_tokens is None
    assert record.reported_cost_usd is None
    await provider.close()


@pytest.mark.asyncio
async def test_markdown_wrapper_is_output_text_protocol(scripting_request) -> None:
    provider, store = _provider(
        lambda request: httpx.Response(
            200,
            json=_envelope("```json\n{}\n```"),
            request=request,
        )
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.OUTPUT_TEXT_PROTOCOL
    )
    assert record.validation_error_message == "Markdown wrappers are not allowed"
    await provider.close()


@pytest.mark.asyncio
async def test_malformed_inner_json_is_classified(scripting_request) -> None:
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope("not-json"), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.INNER_JSON_PARSE
    )
    assert record.validation_error_message == "output text is not valid JSON"
    await provider.close()


@pytest.mark.asyncio
async def test_duplicate_inner_json_key_is_classified_without_persisting_key(
    scripting_request,
) -> None:
    content = '{"title":"safe","SECRET_SENTINEL":1,"SECRET_SENTINEL":2}'
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(content), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.DUPLICATE_JSON_KEY
    )
    assert record.validation_error_message == "duplicate JSON keys are not allowed"
    assert "SECRET_SENTINEL" not in record.model_dump_json()
    await provider.close()


@pytest.mark.asyncio
async def test_production_script_schema_error_retains_bounded_path_and_message(
    scripting_request,
) -> None:
    payload = await _valid_script(scripting_request)
    payload.pop("title")
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(payload), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_SCHEMA
    )
    assert record.validation_error_path == "title"
    assert record.validation_error_message == "Field required"
    assert len(record.validation_error_message) <= 240
    await provider.close()


@pytest.mark.asyncio
async def test_unsupported_field_is_classified_without_persisting_field_value(
    scripting_request,
) -> None:
    payload = await _valid_script(scripting_request)
    payload["unexpected"] = "UNSUPPORTED_VALUE_SENTINEL"
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(payload), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.UNSUPPORTED_FIELD
    )
    assert record.validation_error_path == "unexpected"
    assert record.validation_error_message == "extra fields are not permitted"
    assert "UNSUPPORTED_VALUE_SENTINEL" not in record.model_dump_json()
    await provider.close()


@pytest.mark.asyncio
async def test_production_script_contract_error_is_classified(scripting_request) -> None:
    payload = await _valid_script(scripting_request)
    payload["scenes"][1]["scene_number"] = 1
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(payload), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_CONTRACT
    )
    assert record.validation_error_message == (
        "Value error, scene_number values must be consecutive starting at 1"
    )
    await provider.close()


@pytest.mark.asyncio
async def test_script_vs_plan_mismatch_is_classified(scripting_request) -> None:
    payload = await _valid_script(scripting_request)
    payload["language"] = "es"
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(payload), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is OpenRouterScriptingValidationErrorCode.PLAN_CONTRACT
    assert record.validation_error_path == "language"
    assert record.validation_error_message == "script language does not match production plan"
    await provider.close()


@pytest.mark.asyncio
async def test_scene_count_mismatch_is_classified(scripting_request) -> None:
    payload = await _valid_script(scripting_request)
    payload["scenes"] = [payload["scenes"][0]]
    payload["scenes"][0]["estimated_duration_seconds"] = 20
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(payload), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.SCENE_COUNT_POLICY
    )
    assert record.validation_error_path == "scenes"
    await provider.close()


@pytest.mark.asyncio
async def test_duration_policy_error_is_classified(scripting_request) -> None:
    payload = await _valid_script(scripting_request)
    for scene in payload["scenes"]:
        scene["narration"] = " ".join("word" for _ in range(100))
    provider, store = _provider(
        lambda request: httpx.Response(200, json=_envelope(payload), request=request)
    )
    with pytest.raises(ScriptingProviderContractError):
        await provider.generate_script(scripting_request)
    record = _record(store)
    assert record.validation_error_code is OpenRouterScriptingValidationErrorCode.DURATION_POLICY
    assert record.validation_error_path == "scenes"
    assert record.validation_error_message == (
        "script narration exceeds the requested duration policy"
    )
    await provider.close()


@pytest.mark.asyncio
async def test_valid_response_remains_unchanged_and_retains_safe_http_metadata(
    scripting_request,
) -> None:
    payload = await _valid_script(scripting_request)
    provider, store = _provider(
        lambda request: httpx.Response(
            200,
            json=_envelope(payload),
            headers={"x-request-id": "request-valid-safe"},
            request=request,
        )
    )
    response = await provider.generate_script(scripting_request)
    record = _record(store)
    assert response.script.model_dump(mode="json") == payload
    assert record.status is OpenRouterScriptingRequestStatus.COMPLETED
    assert record.http_status == 200
    assert record.requested_model == "google/gemini-2.5-flash-lite"
    assert record.provider_request_id == "request-valid-safe"
    assert record.reported_model == "google/reported-safe"
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (11, 22, 33)
    assert record.reported_cost_usd == Decimal("0.0004")
    assert record.validation_error_code is None
    assert record.validation_error_path is None
    assert record.validation_error_message is None
    await provider.close()
