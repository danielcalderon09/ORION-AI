"""Offline simulated and OpenRouter visual asset provider contract tests."""

import asyncio
import json

import httpx
import pytest

from backend.src.production.visual_asset_planning.configuration import (
    VisualAssetPlanningConfiguration,
)
from backend.src.production.visual_asset_planning.exceptions import (
    VisualAssetPlanningProviderAuthenticationException,
    VisualAssetPlanningProviderConfigurationException,
    VisualAssetPlanningProviderContractException,
    VisualAssetPlanningProviderRateLimitException,
    VisualAssetPlanningProviderResponseException,
    VisualAssetPlanningProviderTimeoutException,
    VisualAssetPlanningProviderUnavailableException,
    VisualAssetPlanningStructuredOutputException,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProviderRequest,
)
from backend.src.production.visual_asset_planning.prompt_builder import (
    VisualAssetPlanningPromptBuilder,
)
from backend.src.production.visual_asset_planning.providers import (
    SimulatedVisualAssetPlanningProvider,
)
from backend.src.production.visual_asset_planning.providers.openrouter_provider import (
    OpenRouterVisualAssetPlanningProvider,
)
from backend.tests.unit.production.visual_asset_planning.conftest import (
    COMMAND_ID,
    JOB_ID,
)


def request_for(scene_plan, **config):
    return VisualAssetPlanningProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        scene_plan=scene_plan,
        configuration=VisualAssetPlanningConfiguration(**config),
    )


async def valid_payload(scene_plan):
    response = await SimulatedVisualAssetPlanningProvider().generate_visual_asset_plan(
        request_for(scene_plan)
    )
    return response.visual_asset_plan.model_dump(mode="json")


def provider(handler, *, attempts=1, sleeper=asyncio.sleep, **overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    values = {
        "api_key": "fake-visual-only",
        "model": "anthropic/visual-planner",
        "prompt_builder": VisualAssetPlanningPromptBuilder(max_scene_plan_bytes=100_000),
        "client": client,
        "max_transport_attempts": attempts,
        "sleeper": sleeper,
        "monotonic_clock": lambda: 1.0,
    }
    values.update(overrides)
    return OpenRouterVisualAssetPlanningProvider(**values), client


@pytest.mark.asyncio
async def test_simulated_is_deterministic_mapped_and_configurable(
    production_scene_plan,
) -> None:
    provider_instance = SimulatedVisualAssetPlanningProvider()
    request = request_for(
        production_scene_plan,
        images_per_shot=2,
        target_width=1920,
        target_height=1080,
    )
    before = request.model_dump_json()
    first = await provider_instance.generate_visual_asset_plan(request)
    second = await provider_instance.generate_visual_asset_plan(request)
    assert first == second
    assert request.model_dump_json() == before
    assert len(first.visual_asset_plan.assets) == 8
    assert first.visual_asset_plan.aspect_ratio == "16:9"
    assert all(
        asset.camera_intent
        == production_scene_plan.scenes[asset.scene_number - 1].shots[asset.shot_number - 1].camera
        for asset in first.visual_asset_plan.assets
    )
    assert all(asset.expected_duration_seconds == 5 for asset in first.visual_asset_plan.assets)
    assert first.visual_asset_plan.assets[1].reference_asset_ids


@pytest.mark.asyncio
async def test_openrouter_request_headers_schema_and_telemetry(
    production_scene_plan,
) -> None:
    captured = {}
    plan = await valid_payload(production_scene_plan)

    async def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "request-visual-safe",
                "model": "google/reported-visual-planner",
                "choices": [
                    {
                        "message": {"content": json.dumps(plan)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 22,
                    "total_tokens": 33,
                },
            },
            request=request,
        )

    real, client = provider(
        handler,
        http_referer="https://orion.invalid/app",
        app_title="ORION Test",
    )
    response = await real.generate_visual_asset_plan(request_for(production_scene_plan))
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer fake-visual-only"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["headers"]["http-referer"] == "https://orion.invalid/app"
    assert captured["headers"]["x-title"] == "ORION Test"
    payload = captured["payload"]
    assert payload["model"] == "anthropic/visual-planner"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["name"] == ("production_visual_asset_plan")
    assert payload["provider"] == {
        "require_parameters": True,
        "data_collection": "deny",
    }
    assert payload["stream"] is False
    assert payload["store"] is False
    assert response.requested_model == "anthropic/visual-planner"
    assert response.reported_model == "google/reported-visual-planner"
    assert response.total_tokens == 33
    assert response.request_id == "request-visual-safe"
    assert "fake-visual-only" not in response.model_dump_json()
    assert "HTTP-Referer" not in response.model_dump_json()
    await real.close()
    assert client.is_closed


@pytest.mark.asyncio
async def test_optional_telemetry_and_headers_may_be_absent(
    production_scene_plan,
) -> None:
    captured = {}
    plan = await valid_payload(production_scene_plan)

    async def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(plan)}}]},
            request=request,
        )

    real, _ = provider(handler)
    response = await real.generate_visual_asset_plan(request_for(production_scene_plan))
    assert "http-referer" not in captured["headers"]
    assert "x-title" not in captured["headers"]
    assert response.reported_model is None
    assert response.request_id is None
    assert response.total_tokens is None
    assert response.finish_reason is None
    await real.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, VisualAssetPlanningStructuredOutputException),
        (401, VisualAssetPlanningProviderAuthenticationException),
        (403, VisualAssetPlanningProviderAuthenticationException),
        (429, VisualAssetPlanningProviderRateLimitException),
        (500, VisualAssetPlanningProviderUnavailableException),
        (502, VisualAssetPlanningProviderUnavailableException),
        (503, VisualAssetPlanningProviderUnavailableException),
    ],
)
async def test_openrouter_maps_safe_http_errors(
    production_scene_plan,
    status,
    error_type,
) -> None:
    real, _ = provider(lambda request: httpx.Response(status, request=request))
    with pytest.raises(error_type) as captured:
        await real.generate_visual_asset_plan(request_for(production_scene_plan))
    assert "fake-visual-only" not in str(captured.value)
    await real.close()


@pytest.mark.asyncio
async def test_timeout_connection_retries_and_cancel_are_typed(
    production_scene_plan,
) -> None:
    request = request_for(production_scene_plan)

    async def timeout(http_request):
        raise httpx.ReadTimeout("timeout", request=http_request)

    timed, _ = provider(timeout)
    with pytest.raises(VisualAssetPlanningProviderTimeoutException):
        await timed.generate_visual_asset_plan(request)
    await timed.close()

    calls = 0
    delays = []

    async def unavailable(http_request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=http_request)

    async def sleeper(delay):
        delays.append(delay)

    disconnected, _ = provider(unavailable, attempts=3, sleeper=sleeper)
    with pytest.raises(VisualAssetPlanningProviderUnavailableException):
        await disconnected.generate_visual_asset_plan(request)
    assert calls == 3
    assert delays == [0.25, 0.5]
    await disconnected.close()

    async def cancel(_request):
        raise asyncio.CancelledError

    cancelling, _ = provider(cancel)
    with pytest.raises(asyncio.CancelledError):
        await cancelling.generate_visual_asset_plan(request)
    await cancelling.close()


@pytest.mark.asyncio
async def test_invalid_external_json_duplicates_and_contract_are_rejected(
    production_scene_plan,
) -> None:
    request = request_for(production_scene_plan)
    invalid_outer, _ = provider(
        lambda http_request: httpx.Response(
            200,
            content=b"{",
            request=http_request,
        )
    )
    with pytest.raises(VisualAssetPlanningProviderResponseException):
        await invalid_outer.generate_visual_asset_plan(request)
    await invalid_outer.close()

    duplicate, _ = provider(
        lambda http_request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"schema_version":"1.0.0","schema_version":"1.0.0"}'}}
                ]
            },
            request=http_request,
        )
    )
    with pytest.raises(VisualAssetPlanningProviderContractException):
        await duplicate.generate_visual_asset_plan(request)
    await duplicate.close()

    plan = await valid_payload(production_scene_plan)
    plan["language"] = "en"
    invalid_contract, _ = provider(
        lambda http_request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(plan)}}]},
            request=http_request,
        )
    )
    with pytest.raises(VisualAssetPlanningProviderContractException):
        await invalid_contract.generate_visual_asset_plan(request)
    await invalid_contract.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": ""},
        {"model": ""},
        {"base_url": "http://openrouter.ai/api/v1"},
        {"base_url": "https:///api/v1"},
        {"base_url": "https://user:pass@openrouter.ai/api/v1"},
        {"max_transport_attempts": 6},
        {"temperature": 3},
    ],
)
def test_provider_configuration_rejects_unsafe_values(overrides) -> None:
    values = {
        "api_key": "fake-only",
        "model": "qwen/model",
        "prompt_builder": VisualAssetPlanningPromptBuilder(max_scene_plan_bytes=1000),
    }
    values.update(overrides)
    with pytest.raises(VisualAssetPlanningProviderConfigurationException):
        OpenRouterVisualAssetPlanningProvider(**values)
