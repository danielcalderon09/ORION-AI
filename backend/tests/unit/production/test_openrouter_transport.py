"""OpenRouter transport contracts; every request uses MockTransport."""

import asyncio

import httpx
import pytest

from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleAuthenticationError,
    OpenAICompatibleProtocolError,
    OpenAICompatibleRateLimitError,
    OpenAICompatibleResponsesClient,
    OpenAICompatibleUnavailableError,
)


def client(handler, **overrides):
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    options = {
        "api_key": "fake-openrouter-key",
        "base_url": "https://openrouter.ai/api/v1",
        "timeout_seconds": 5,
        "max_transport_attempts": 1,
        "retry_base_delay_seconds": 0.01,
        "client": http_client,
    }
    options.update(overrides)
    return OpenAICompatibleResponsesClient(**options), http_client


@pytest.mark.parametrize(
    "base_url",
    [
        "http://openrouter.ai/api/v1",
        "https://user:password@openrouter.ai/api/v1",
        "https:///api/v1",
    ],
)
def test_transport_rejects_unsafe_base_urls(base_url) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleResponsesClient(
            api_key="fake",
            base_url=base_url,
            timeout_seconds=1,
            max_transport_attempts=1,
            retry_base_delay_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_endpoint_authorization_and_optional_headers() -> None:
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    transport, http_client = client(
        handler,
        http_referer="https://orion.example/app",
        app_title="ORION AI",
    )
    await transport.post({"model": "openai/test"})
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer fake-openrouter-key"
    assert captured["headers"]["http-referer"] == "https://orion.example/app"
    assert captured["headers"]["x-title"] == "ORION AI"
    await transport.close()
    assert http_client.is_closed


@pytest.mark.asyncio
async def test_optional_headers_are_absent_by_default() -> None:
    captured = {}

    async def handler(request):
        captured.update(request.headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    transport, _ = client(handler)
    await transport.post({})
    assert "http-referer" not in captured
    assert "x-title" not in captured
    await transport.close()


@pytest.mark.asyncio
async def test_invalid_or_duplicate_response_json_is_rejected() -> None:
    for content in (b"not-json", b'{"choices":[],"choices":[]}'):
        transport, _ = client(
            lambda request, response_content=content: httpx.Response(
                200, content=response_content
            )
        )
        with pytest.raises(OpenAICompatibleProtocolError):
            await transport.post({})
        await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_nonretryable_statuses_are_attempted_once(status) -> None:
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    transport, _ = client(handler, max_transport_attempts=3)
    error = OpenAICompatibleAuthenticationError if status in {401, 403} else OpenAICompatibleProtocolError
    with pytest.raises(error):
        await transport.post({})
    assert calls == 1
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_retryable_statuses_honor_attempt_limit(status) -> None:
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async def no_delay(delay):
        await asyncio.sleep(0)

    transport, _ = client(
        handler,
        max_transport_attempts=3,
        sleeper=no_delay,
    )
    error = (
        OpenAICompatibleRateLimitError
        if status == 429
        else OpenAICompatibleUnavailableError
    )
    with pytest.raises(error):
        await transport.post({})
    assert calls == 3
    await transport.close()
