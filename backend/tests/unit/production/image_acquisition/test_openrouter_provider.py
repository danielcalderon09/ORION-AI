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
from backend.src.production.image_acquisition.diagnostics import (
    ImageDiagnosticSubtype,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderAuthenticationException,
    ImageAcquisitionProviderConfigurationException,
    ImageAcquisitionProviderContractException,
    ImageAcquisitionProviderModelException,
    ImageAcquisitionProviderPolicyException,
    ImageAcquisitionProviderRateLimitException,
    ImageAcquisitionProviderResponseException,
    ImageAcquisitionProviderUnavailableException,
    ImageAcquisitionProviderUncertainException,
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
        "owns_client": True,
        "sleeper": _no_sleep,
    }
    values.update(updates)
    return OpenRouterImageAcquisitionProvider(**values)


async def _no_sleep(delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_close_is_idempotent_and_preserves_injected_client() -> None:
    injected = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    image_provider = OpenRouterImageAcquisitionProvider(
        api_key="test-key-not-real",
        model="openai/test-image-model",
        prompt_builder=ImageGenerationPromptBuilder(),
        client=injected,
    )

    await image_provider.close()
    await image_provider.close()

    assert injected.is_closed is False
    await injected.aclose()


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
    assert payload["resolution"] == "1K"
    assert "size" not in payload
    assert payload["aspect_ratio"] == "1:1"
    assert result.images[0].provider_metadata["width"] == 64
    assert result.images[0].provider_metadata["height"] == 64
    assert result.images[0].provider_metadata["diagnostic"] == {
        "declared_media_type": "image/png",
        "detected_media_type": "image/png",
        "decoded_width": 64,
        "decoded_height": 64,
        "decoded_format": "PNG",
        "decoded_size_bytes": len(png_bytes()),
        "expected_width": 64,
        "expected_height": 64,
        "expected_aspect_ratio": 1.0,
        "actual_aspect_ratio": 1.0,
        "requested_output_format": "png",
    }
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
            json={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]},
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
        {"base_url": "https://example.invalid/api/v1"},
        {"base_url": "https://openrouter.ai/api/v1/other"},
        {"provider_only": "unsafe/provider"},
        {"max_transport_attempts": 6},
    ],
)
def test_configuration_rejects_unsafe_values(updates) -> None:
    with pytest.raises(ImageAcquisitionProviderConfigurationException):
        provider(httpx.MockTransport(lambda _: httpx.Response(200)), **updates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type", "expected", "subtype"),
    [
        (
            400,
            "invalid_request",
            ImageAcquisitionProviderContractException,
            ImageDiagnosticSubtype.PROVIDER_HTTP_ERROR,
        ),
        (
            400,
            "unsupported_parameter",
            ImageAcquisitionProviderContractException,
            ImageDiagnosticSubtype.PROVIDER_HTTP_ERROR,
        ),
        (
            401,
            "authentication",
            ImageAcquisitionProviderAuthenticationException,
            ImageDiagnosticSubtype.PROVIDER_AUTHENTICATION,
        ),
        (
            403,
            "permission_denied",
            ImageAcquisitionProviderAuthenticationException,
            ImageDiagnosticSubtype.PROVIDER_AUTHENTICATION,
        ),
        (
            404,
            "model_not_found",
            ImageAcquisitionProviderModelException,
            ImageDiagnosticSubtype.PROVIDER_MODEL,
        ),
        (
            400,
            "content_policy_violation",
            ImageAcquisitionProviderPolicyException,
            ImageDiagnosticSubtype.PROVIDER_POLICY,
        ),
        (
            429,
            "rate_limit_exceeded",
            ImageAcquisitionProviderRateLimitException,
            ImageDiagnosticSubtype.PROVIDER_RATE_LIMIT,
        ),
        (
            500,
            "server_error",
            ImageAcquisitionProviderUnavailableException,
            ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE,
        ),
        (
            502,
            "provider_unavailable",
            ImageAcquisitionProviderUnavailableException,
            ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE,
        ),
        (
            503,
            "provider_overloaded",
            ImageAcquisitionProviderUnavailableException,
            ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE,
        ),
        (
            504,
            "timeout",
            ImageAcquisitionProviderUnavailableException,
            ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE,
        ),
    ],
)
async def test_http_error_classification(
    visual_asset_plan,
    status,
    error_type,
    expected,
    subtype,
) -> None:
    attempts = 0

    def handle(_):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            status,
            json={
                "id": "safe-error-request-id",
                "model": "reported/error-model",
                "error": {
                    "type": error_type,
                    "message": "external detail must not escape",
                },
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                    "cost": "0.0001",
                },
            },
        )

    client = provider(
        httpx.MockTransport(handle),
        max_transport_attempts=1,
    )
    with pytest.raises(expected) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert "external detail" not in str(raised.value)
    assert raised.value.http_status == status
    assert raised.value.diagnostic_subtype is subtype
    assert raised.value.provider_request_id == "safe-error-request-id"
    assert raised.value.reported_model == "reported/error-model"
    assert raised.value.total_tokens == 3
    assert str(raised.value.cost_usd) == "0.0001"
    assert attempts == 1
    await client.close()


@pytest.mark.asyncio
async def test_timeout_connection_and_cancel_propagate(
    visual_asset_plan,
) -> None:
    errors = (
        (
            httpx.ReadTimeout("timeout"),
            ImageAcquisitionProviderUncertainException,
        ),
        (
            httpx.ConnectError("connection"),
            ImageAcquisitionProviderUncertainException,
        ),
    )
    for transport_error, expected in errors:

        def handle(http_request, error=transport_error):
            raise error

        client = provider(httpx.MockTransport(handle), max_transport_attempts=1)
        with pytest.raises(expected) as raised:
            await client.generate_image(request(visual_asset_plan.assets[0]))
        assert raised.value.diagnostic_subtype is ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT
        await client.close()

    def cancel(_):
        raise asyncio.CancelledError

    client = provider(httpx.MockTransport(cancel))
    with pytest.raises(asyncio.CancelledError):
        await client.generate_image(request(visual_asset_plan.assets[0]))
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "subtype"),
    [
        (b"not-json", ImageDiagnosticSubtype.PROVIDER_ENVELOPE),
        (b'{"data":[]}', ImageDiagnosticSubtype.MISSING_IMAGE),
        (
            b'{"data":[{"url":"https://example.invalid/image.png"}]}',
            ImageDiagnosticSubtype.PROVIDER_ENVELOPE,
        ),
        (b'{"data":[{"b64_json":"%%%"}]}', ImageDiagnosticSubtype.INVALID_BASE64),
        (b'{"data":[{"b64_json":""}]}', ImageDiagnosticSubtype.MISSING_IMAGE),
        (b'{"error":{"type":"provider_error"}}', ImageDiagnosticSubtype.PROVIDER_BODY_ERROR),
        (
            (b'{"data":[{"b64_json":"' + base64.b64encode(b"not-an-image") + b'"}]}'),
            ImageDiagnosticSubtype.INVALID_IMAGE_SIGNATURE,
        ),
        (
            (
                b'{"data":[{"b64_json":"'
                + base64.b64encode(png_bytes())
                + b'","media_type":"image/gif"}]}'
            ),
            ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT,
        ),
        (
            (
                b'{"data":[{"b64_json":"'
                + base64.b64encode(png_bytes())
                + b'"},{"b64_json":"'
                + base64.b64encode(png_bytes())
                + b'"}]}'
            ),
            ImageDiagnosticSubtype.MULTIPLE_IMAGES,
        ),
        (
            (b'{"data":[{"b64_json":"' + base64.b64encode(b"<svg></svg>") + b'"}]}'),
            ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT,
        ),
        (b'{"data":[],"data":[]}', ImageDiagnosticSubtype.PROVIDER_ENVELOPE),
    ],
)
async def test_rejects_invalid_or_active_responses(
    visual_asset_plan,
    body,
    subtype,
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
    with pytest.raises(ImageAcquisitionProviderResponseException) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert raised.value.http_status == 200
    assert raised.value.diagnostic_subtype is subtype
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
            json={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]},
        )

    transport = httpx.MockTransport(handle)
    async_client = httpx.AsyncClient(transport=transport)
    client = provider(transport, client=async_client)
    await client.generate_image(request(visual_asset_plan.assets[0]))
    await client.close()
    assert calls == 1
    assert async_client.is_closed


@pytest.mark.asyncio
async def test_rejects_oversized_decoded_image(visual_asset_plan) -> None:
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]},
            )
        ),
        max_decoded_image_bytes=32,
    )
    with pytest.raises(ImageAcquisitionProviderResponseException) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert raised.value.diagnostic_subtype is ImageDiagnosticSubtype.DECODED_IMAGE_TOO_LARGE
    await client.close()


@pytest.mark.asyncio
async def test_rejects_image_outside_planned_aspect_ratio(visual_asset_plan) -> None:
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(png_bytes((64, 32))).decode()}]},
            )
        )
    )
    with pytest.raises(
        ImageAcquisitionProviderResponseException,
        match="aspect ratio",
    ) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert raised.value.diagnostic_subtype is ImageDiagnosticSubtype.ASPECT_RATIO_MISMATCH
    assert raised.value.diagnostic_metadata is not None
    assert raised.value.diagnostic_metadata.decoded_width == 64
    assert raised.value.diagnostic_metadata.decoded_height == 32
    await client.close()


@pytest.mark.asyncio
async def test_mime_mismatch_preserves_only_technical_metadata(visual_asset_plan) -> None:
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "id": "safe-request-id",
                    "model": "reported/model",
                    "data": [
                        {
                            "b64_json": base64.b64encode(png_bytes()).decode(),
                            "media_type": "image/jpeg",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                        "cost": "0.0002",
                    },
                },
            )
        )
    )
    with pytest.raises(ImageAcquisitionProviderResponseException) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    error = raised.value
    assert error.diagnostic_subtype is ImageDiagnosticSubtype.MIME_MISMATCH
    assert error.provider_request_id == "safe-request-id"
    assert error.reported_model == "reported/model"
    assert error.total_tokens == 3
    assert str(error.cost_usd) == "0.0002"
    assert error.diagnostic_metadata is not None
    assert error.diagnostic_metadata.declared_media_type == "image/jpeg"
    assert error.diagnostic_metadata.detected_media_type == "image/png"
    await client.close()


@pytest.mark.asyncio
async def test_response_model_validation_is_sanitized(visual_asset_plan) -> None:
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "id": "safe-id",
                    "model": "x" * 301,
                    "data": [{"b64_json": base64.b64encode(png_bytes()).decode()}],
                },
            )
        )
    )
    with pytest.raises(ImageAcquisitionProviderContractException) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    error = raised.value
    assert error.diagnostic_subtype is ImageDiagnosticSubtype.RESPONSE_MODEL_VALIDATION
    assert error.validation_error_path == "reported_model"
    assert error.validation_error_message is not None
    assert "x" * 20 not in error.validation_error_message
    assert error.provider_request_id == "safe-id"
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b"\x89PNG\r\n\x1a\ntruncated",
        b"\xff\xd8\xfftruncated",
        b"RIFF\x10\x00\x00\x00WEBPtruncated",
    ],
)
async def test_recognized_but_undecodable_images_are_classified(
    visual_asset_plan,
    content,
) -> None:
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(content).decode()}]},
            )
        )
    )
    with pytest.raises(ImageAcquisitionProviderResponseException) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert raised.value.diagnostic_subtype is ImageDiagnosticSubtype.UNDECODABLE_IMAGE
    assert raised.value.diagnostic_metadata is not None
    assert raised.value.diagnostic_metadata.decoded_size_bytes == len(content)
    await client.close()


class _DecodedImage:
    def __init__(self, *, size, decoded_format) -> None:
        self.size = size
        self.format = decoded_format

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def verify(self) -> None:
        return None

    def load(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "decoded_format", "subtype"),
    [
        ((0, 64), "PNG", ImageDiagnosticSubtype.INVALID_DIMENSIONS),
        ((64, 64), "GIF", ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT),
        ((10_000, 5_000), "PNG", ImageDiagnosticSubtype.DECODED_IMAGE_TOO_LARGE),
    ],
)
async def test_decoded_dimension_and_format_failures_are_distinct(
    monkeypatch,
    visual_asset_plan,
    size,
    decoded_format,
    subtype,
) -> None:
    monkeypatch.setattr(
        "backend.src.production.image_acquisition.providers.openrouter_provider.Image.open",
        lambda _: _DecodedImage(size=size, decoded_format=decoded_format),
    )
    content = png_bytes()
    client = provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(content).decode()}]},
            )
        )
    )
    with pytest.raises(ImageAcquisitionProviderResponseException) as raised:
        await client.generate_image(request(visual_asset_plan.assets[0]))
    assert raised.value.diagnostic_subtype is subtype
    assert raised.value.diagnostic_metadata is not None
    if size[0] > 0:
        assert raised.value.diagnostic_metadata.decoded_width == size[0]
    await client.close()
