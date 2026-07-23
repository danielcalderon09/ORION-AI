"""OpenRouter dedicated Images API tests using only MockTransport."""

import asyncio
import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderAuthenticationException,
    ImageAcquisitionProviderConfigurationException,
    ImageAcquisitionProviderContractException,
    ImageAcquisitionProviderModelException,
    ImageAcquisitionProviderPolicyException,
    ImageAcquisitionProviderRateLimitException,
    ImageAcquisitionProviderResponseException,
    ImageAcquisitionProviderTimeoutException,
    ImageAcquisitionProviderUnavailableException,
)
from backend.src.production.image_acquisition.ports import (
    ImageAcquisitionProviderRequest,
)
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.providers.openrouter_provider import (
    OpenRouterImageAcquisitionProvider,
)
from backend.tests.unit.production.image_acquisition.conftest import (
    COMMAND_ID,
    JOB_ID,
)


def png_bytes(size=(64, 64)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, "navy").save(stream, "PNG")
    return stream.getvalue()


def request(asset) -> ImageAcquisitionProviderRequest:
    return ImageAcquisitionProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        visual_asset=asset,
        configuration=ImageAcquisitionConfiguration(),
    )


def provider(transport, **updates) -> OpenRouterImageAcquisitionProvider:
    values = {
        "api_key": "test-key-not-real",
        "model": "openai/test-image-model",
        "prompt_builder": ImageGenerationPromptBuilder(),
        "client": httpx.AsyncClient(transport=transport),
        "sleeper": _no_sleep,
    }
    values.update(updates)
    return OpenRouterImageAcquisitionProvider(**values)


async def _no_sleep(delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_request_contract_headers_and_optional_routing(
    visual_asset_plan,
) -> None:
    observed = {}

    async def handle(http_request):
        observed["request"] = http_request
        observed["payload"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "id": "request-body-id",
                "model": "reported/model",
                "data": [
                    {
                        "b64_json": base64.b64encode(png_bytes()).decode(),
                        "media_type": "image/png",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                    "cost": "0.00125",
                },
            },
            headers={"x-request-id": "request-header-id"},
        )

    client = provider(
        httpx.MockTransport(handle),
        provider_only="test-provider",
        http_referer="https://orion.invalid",
        app_title="ORION Test",
    )
    result = await client.generate_image(request(visual_asset_plan.assets[0]))
    sent = observed["request"]
    payload = observed["payload"]
    assert sent.url == httpx.URL("https://openrouter.ai/api/v1/images")
    assert sent.headers["Authorization"] == "Bearer test-key-not-real"
    assert sent.headers["Content-Type"] == "application/json"
    assert sent.headers["HTTP-Referer"] == "https://orion.invalid"
    assert sent.headers["X-Title"] == "ORION Test"
    assert payload["model"] == "openai/test-image-model"
    assert payload["n"] == 1
    assert payload["size"] == "64x64"
    assert payload["aspect_ratio"] == "1:1"
    assert payload["output_format"] == "png"
    assert payload["stream"] is False
    assert payload["provider"] == {
        "allow_fallbacks": False,
        "only": ["test-provider"],
    }
    assert result.request_id == "request-header-id"
    assert result.reported_model == "reported/model"
    assert result.total_tokens == 16
    assert str(result.cost_usd) == "0.00125"
    assert "test-key-not-real" not in repr(result)
    assert "Authorization" not in result.metadata
    await client.close()


@pytest.mark.asyncio
async def test_optional_response_and_headers_may_be_absent(
    visual_asset_plan,
) -> None:
    observed = {}

    def handle(http_request):
        observed["headers"] = http_request.headers
        return httpx.Response(
            200,
            json={
                "data": [
                    {"b64_json": base64.b64encode(png_bytes()).decode()}
                ]
            },
        )

    client = provider(httpx.MockTransport(handle))
    result = await client.generate_image(request(visual_asset_plan.assets[0]))
    assert "HTTP-Referer" not in observed["headers"]
    assert "X-Title" not in observed["headers"]
    assert result.reported_model is None
    assert result.request_id is None
    assert result.total_tokens is None
    assert result.cost_usd is None
    await client.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"api_key": ""},
        {"model": ""},
        {"base_url": "http://openrouter.ai/api/v1"},
        {"base_url": "https:///api/v1"},
        {"base_url": "https://user:pass@openrouter.ai/api/v1"},
        {"provider_only": "unsafe/provider"},
        {"max_transport_attempts": 6},
    ],
)
def test_configuration_rejects_unsafe_values(updates) -> None:
    with pytest.raises(ImageAcquisitionProviderConfigurationException):
        provider(httpx.MockTransport(lambda _: httpx.Response(200)), **updates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [
        (400, "invalid_request", ImageAcquisitionProviderContractException),
        (400, "unsupported_parameter", ImageAcquisitionProviderContractException),
        (401, "authentication", ImageAcquisitionProviderAuthenticationException),
        (403, "permission_denied", ImageAcquisitionProviderAuthenticationException),
        (404, "model_not_found", ImageAcquisitionProviderModelException),
        (400, "content_policy_violation", ImageAcquisitionProviderPolicyException),
        (429, "rate_limit_exceeded", ImageAcquisitionProviderRateLimitException),
        (500, "server_error", ImageAcquisitionProviderUnavailableException),
        (502, "provider_unavailable", ImageAcquisitionProviderUnavailableException),
        (503, "provider_overloaded", ImageAcquisitionProviderUnavailableException),
        (504, "timeout", ImageAcquisitionProviderUnavailableException),
    ],
)
async def test_http_error_classification(
    visual_asset_plan,
    status,
    error_type,
    expected,
) -> None:
    attempts = 0

    def handle(_):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            status,
            json={
                "error": {
                    "type": error_type,
                    "message": "external detail must not escape",
                }
            },
        )

    client = provider(
        httpx.MockTransport(handle),
        max_transport_attempts=2,
    )
    with pytest.raises(expected) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert "external detail" not in str(raised.value)
    assert attempts == (2 if status in {429, 500, 502, 503, 504} else 1)
    await client.close()


@pytest.mark.asyncio
async def test_timeout_connection_and_cancel_propagate(
    visual_asset_plan,
) -> None:
    errors = (
        (
            httpx.ReadTimeout("timeout"),
            ImageAcquisitionProviderTimeoutException,
        ),
        (
            httpx.ConnectError("connection"),
            ImageAcquisitionProviderUnavailableException,
        ),
    )
    for transport_error, expected in errors:
        def handle(http_request, error=transport_error):
            raise error

        client = provider(httpx.MockTransport(handle), max_transport_attempts=1)
        with pytest.raises(expected):
            await client.generate_image(request(visual_asset_plan.assets[0]))
        await client.close()

    def cancel(_):
        raise asyncio.CancelledError

    client = provider(httpx.MockTransport(cancel))
    with pytest.raises(asyncio.CancelledError):
        await client.generate_image(request(visual_asset_plan.assets[0]))
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"data":[]}',
        b'{"data":[{"url":"https://example.invalid/image.png"}]}',
        b'{"data":[{"b64_json":"%%%"}]}',
        b'{"data":[{"b64_json":""}]}',
        (
            b'{"data":[{"b64_json":"'
            + base64.b64encode(b"<svg></svg>")
            + b'"}]}'
        ),
        b'{"data":[],"data":[]}',
    ],
)
async def test_rejects_invalid_or_active_responses(
    visual_asset_plan,
    body,
) -> None:
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/json"},
            )
        )
    )
    with pytest.raises(ImageAcquisitionProviderResponseException):
        await client.generate_image(request(visual_asset_plan.assets[0]))
    await client.close()


@pytest.mark.asyncio
async def test_client_closes_and_never_calls_real_network(
    visual_asset_plan,
) -> None:
    calls = 0

    def handle(_):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "data": [
                    {"b64_json": base64.b64encode(png_bytes()).decode()}
                ]
            },
        )

    transport = httpx.MockTransport(handle)
    async_client = httpx.AsyncClient(transport=transport)
    client = provider(transport, client=async_client)
    await client.generate_image(request(visual_asset_plan.assets[0]))
    await client.close()
    assert calls == 1
    assert async_client.is_closed
