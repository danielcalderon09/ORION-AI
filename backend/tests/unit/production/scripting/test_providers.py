"""Contract and fake-transport tests for scripting providers."""

import asyncio
import json

import httpx
import pytest

from backend.src.production.scripting.exceptions import (
    ScriptingProviderAuthenticationError,
    ScriptingProviderContractError,
    ScriptingProviderRateLimitError,
    ScriptingProviderTimeoutError,
    ScriptingProviderUnavailableError,
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


def provider(handler, *, attempts=1, sleeper=asyncio.sleep, **provider_options):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
    )
    return (
        OpenRouterScriptingProvider(
            api_key="fake-test-key",
            model="google/script-requested",
            prompt_builder=ScriptingPromptBuilder(max_plan_bytes=100_000),
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
    assert captured["provider"] == {"require_parameters": True, "data_collection": "deny"}
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
        (503, ScriptingProviderUnavailableError),
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
async def test_openrouter_provider_limits_retries_and_propagates_cancellation(
    scripting_request,
) -> None:
    calls = 0
    delays = []
    payload = await valid_script_payload(scripting_request)

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429 if calls == 1 else 200,
            json=body(payload),
            request=request,
        )

    async def sleeper(delay):
        delays.append(delay)

    real, _ = provider(handler, attempts=2, sleeper=sleeper)
    await real.generate_script(scripting_request)
    assert calls == 2
    assert delays == [0.25]
    await real.close()

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
    with pytest.raises(ScriptingProviderTimeoutError):
        await real.generate_script(scripting_request)
    await real.close()

    async def connection(request):
        raise httpx.ConnectError("connection", request=request)

    real, _ = provider(connection)
    with pytest.raises(ScriptingProviderUnavailableError):
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
