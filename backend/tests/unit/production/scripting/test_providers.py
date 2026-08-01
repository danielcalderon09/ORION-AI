"""Contract and fake-transport tests for scripting providers."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.scripting.exceptions import (
    ScriptingProviderAuthenticationError,
    ScriptingProviderConfigurationError,
    ScriptingProviderContractError,
    ScriptingProviderRateLimitError,
    ScriptingProviderResponseError,
    ScriptingProviderUncertainError,
)
from backend.src.production.scripting.openrouter_billable_gate import (
    OpenRouterScriptingBillablePolicy,
)
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingRequestStatus,
)
from backend.src.production.scripting.openrouter_request_store import (
    InMemoryOpenRouterScriptingRequestStore,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)


async def valid_script_payload(scripting_request) -> dict:
    response = await SimulatedScriptingProvider().generate_script(scripting_request)
    return response.script.model_dump(mode="json")


def body(payload: dict | str) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "id": "generation-script-safe",
        "model": "anthropic/script-reported",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
    }


def provider(handler, *, attempts=1, sleeper=asyncio.sleep, store=None, **provider_options):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
    )
    return (
        OpenRouterScriptingProvider(
            api_key="fake-test-key",
            model="google/script-requested",
            prompt_builder=ScriptingPromptBuilder(max_plan_bytes=100_000),
            request_store=store or InMemoryOpenRouterScriptingRequestStore(),
            billable_policy=OpenRouterScriptingBillablePolicy(
                allow_billable_requests=True,
                estimated_cost_usd=Decimal("0.01"),
                maximum_authorized_cost_usd=Decimal("0.10"),
            ),
            client=client,
            owns_client=True,
            max_transport_attempts=attempts,
            sleeper=sleeper,
            monotonic_clock=lambda: 1.0,
            **provider_options,
        ),
        client,
    )


@pytest.mark.asyncio
async def test_simulated_provider_is_deterministic_and_preserves_request(
    scripting_request,
) -> None:
    scripting_request_snapshot = scripting_request.model_dump_json()
    simulated = SimulatedScriptingProvider()
    assert await simulated.generate_script(scripting_request) == await simulated.generate_script(
        scripting_request
    )
    response = await simulated.generate_script(scripting_request)
    assert [scene.source_scene_number for scene in response.script.scenes] == [1, 2]
    assert response.requested_model == response.reported_model
    assert scripting_request.model_dump_json() == scripting_request_snapshot


@pytest.mark.asyncio
async def test_openrouter_provider_sends_schema_and_preserves_usage(
    scripting_request,
) -> None:
    captured = {}
    payload = await valid_script_payload(scripting_request)

    async def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=body(payload),
            headers={"x-request-id": "request-script-safe"},
            request=request,
        )

    real, client = provider(handler)
    response = await real.generate_script(scripting_request)
    assert captured["response_format"]["json_schema"]["name"] == "production_script"
    assert captured["provider"] == {
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    assert captured["model"] == "google/script-requested"
    assert captured["store"] is False
    assert response.total_tokens == 33
    assert response.request_id == "request-script-safe"
    assert response.requested_model == "google/script-requested"
    assert response.reported_model == "anthropic/script-reported"
    await real.close()
    assert client.is_closed


@pytest.mark.asyncio
async def test_openrouter_headers_are_sent_but_never_exposed(scripting_request) -> None:
    captured_headers = {}
    payload = await valid_script_payload(scripting_request)
    response_body = body(payload)
    response_body.pop("id")
    response_body.pop("model")
    response_body.pop("usage")

    async def handler(request):
        captured_headers.update(request.headers)
        return httpx.Response(200, json=response_body, request=request)

    real, _ = provider(
        handler,
        http_referer="https://orion.example",
        app_title="ORION AI",
    )
    response = await real.generate_script(scripting_request)
    assert captured_headers["http-referer"] == "https://orion.example"
    assert captured_headers["x-title"] == "ORION AI"
    assert response.reported_model is None
    assert response.request_id is None
    assert response.total_tokens is None
    serialized = response.model_dump_json()
    assert "fake-test-key" not in serialized
    assert "HTTP-Referer" not in serialized
    assert "X-Title" not in serialized
    await real.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ScriptingProviderAuthenticationError),
        (429, ScriptingProviderRateLimitError),
        (503, ScriptingProviderUncertainError),
    ],
)
async def test_openrouter_provider_translates_safe_status_errors(
    scripting_request, status, error_type
) -> None:
    real, _ = provider(lambda request: httpx.Response(status, request=request))
    with pytest.raises(error_type) as captured:
        await real.generate_script(scripting_request)
    assert "fake-test-key" not in str(captured.value)
    await real.close()


@pytest.mark.asyncio
async def test_openrouter_provider_forbids_retries_and_propagates_cancellation(
    scripting_request,
) -> None:
    with pytest.raises(ScriptingProviderConfigurationError, match="automatic retries"):
        provider(lambda request: httpx.Response(429, request=request), attempts=2)

    async def cancelled(request):
        raise asyncio.CancelledError

    real, _ = provider(cancelled)
    with pytest.raises(asyncio.CancelledError):
        await real.generate_script(scripting_request)
    await real.close()


@pytest.mark.asyncio
async def test_openrouter_provider_translates_timeout_connection_and_bad_contract(
    scripting_request,
) -> None:
    async def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    real, _ = provider(timeout)
    with pytest.raises(ScriptingProviderUncertainError):
        await real.generate_script(scripting_request)
    await real.close()

    async def connection(request):
        raise httpx.ConnectError("connection", request=request)

    real, _ = provider(connection)
    with pytest.raises(ScriptingProviderUncertainError):
        await real.generate_script(scripting_request)
    await real.close()

    real, _ = provider(lambda request: httpx.Response(200, json=body("not-json"), request=request))
    with pytest.raises(ScriptingProviderContractError):
        await real.generate_script(scripting_request)
    await real.close()

    wrong = await valid_script_payload(scripting_request)
    wrong["scenes"][0]["source_scene_number"] = 2
    wrong["scenes"][1]["source_scene_number"] = 1
    real, _ = provider(lambda request: httpx.Response(200, json=body(wrong), request=request))
    with pytest.raises(ScriptingProviderContractError):
        await real.generate_script(scripting_request)
    await real.close()


@pytest.mark.asyncio
async def test_openrouter_durable_checkpoint_precedes_request_and_completed_replay_is_free(
    scripting_request,
) -> None:
    store = InMemoryOpenRouterScriptingRequestStore()
    calls = 0
    payload = await valid_script_payload(scripting_request)

    async def respond(request):
        nonlocal calls
        calls += 1
        response = body(payload)
        response["usage"]["cost"] = 0.004
        return httpx.Response(200, json=response, request=request)

    real, _ = provider(respond, store=store)
    first = await real.generate_script(scripting_request)
    second = await real.generate_script(scripting_request)
    record = next(iter(store.records.values()))
    assert calls == 1
    assert store.checkpoints == 3
    assert record.status is OpenRouterScriptingRequestStatus.COMPLETED
    assert record.reported_cost_usd == Decimal("0.004")
    assert record.script == first.script == second.script
    assert second.metadata["recovered"] is True
    assert "fake-test-key" not in record.model_dump_json()
    await real.close()


@pytest.mark.asyncio
async def test_ambiguous_submission_becomes_uncertain_and_never_resubmits(
    scripting_request,
) -> None:
    store = InMemoryOpenRouterScriptingRequestStore()
    calls = 0

    async def timeout(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("ambiguous", request=request)

    real, _ = provider(timeout, store=store)
    with pytest.raises(ScriptingProviderUncertainError):
        await real.generate_script(scripting_request)
    with pytest.raises(ScriptingProviderUncertainError):
        await real.generate_script(scripting_request)
    record = next(iter(store.records.values()))
    assert calls == 1
    assert record.status is OpenRouterScriptingRequestStatus.UNCERTAIN
    assert record.script is None
    await real.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "```json\n{}\n```",
        '{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        '{"target_duration_seconds":NaN}',
    ],
)
async def test_malformed_structured_output_is_failed_without_script_or_retry(
    scripting_request,
    content,
) -> None:
    store = InMemoryOpenRouterScriptingRequestStore()
    real, _ = provider(
        lambda request: httpx.Response(200, json=body(content), request=request),
        store=store,
    )
    with pytest.raises(ScriptingProviderContractError):
        await real.generate_script(scripting_request)
    record = next(iter(store.records.values()))
    assert record.status is OpenRouterScriptingRequestStatus.FAILED
    assert record.script is None
    with pytest.raises(ScriptingProviderContractError, match="cannot be retried"):
        await real.generate_script(scripting_request)
    await real.close()


@pytest.mark.asyncio
async def test_oversized_provider_response_is_failed_safely(scripting_request) -> None:
    payload = await valid_script_payload(scripting_request)
    real, _ = provider(
        lambda request: httpx.Response(200, json=body(payload), request=request),
        max_response_bytes=100,
    )
    with pytest.raises(ScriptingProviderResponseError):
        await real.generate_script(scripting_request)
    await real.close()
