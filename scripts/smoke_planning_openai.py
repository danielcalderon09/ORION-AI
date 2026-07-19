"""Smoke-check optional OpenAI planning support without making a request."""

import asyncio
from importlib.metadata import requires

import httpx

from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers.openai_provider import (
    OpenAIPlanningProvider,
)


async def smoke() -> None:
    package_requirements = requires("orion-ai") or []
    assert any(
        requirement.startswith("httpx<1.0,>=0.27")
        and "planning-openai" in requirement
        for requirement in package_requirements
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, request=request)
        ),
        base_url="https://api.openai.com/v1",
    )
    provider = OpenAIPlanningProvider(
        api_key="smoke-test-only",
        model="gpt-smoke-versioned",
        prompt_builder=PlanningPromptBuilder(),
        client=client,
        max_transport_attempts=1,
    )
    await provider.close()
    assert client.is_closed
    print("planning-openai installation smoke: OK (zero requests)")


if __name__ == "__main__":
    asyncio.run(smoke())
