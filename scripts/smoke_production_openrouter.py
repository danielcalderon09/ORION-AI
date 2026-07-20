"""Smoke-check optional OpenRouter support without making a request."""

import asyncio
import tomllib
from pathlib import Path

import httpx

from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers.openrouter_provider import (
    OpenRouterPlanningProvider,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)


async def smoke() -> None:
    root = Path(__file__).resolve().parents[1]
    extras = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]
    assert extras["production-llm"] == ["httpx>=0.27,<1.0"]
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None))
    planning = OpenRouterPlanningProvider(
        api_key="smoke-test-only",
        model="openai/smoke-model",
        prompt_builder=PlanningPromptBuilder(),
        client=client,
        max_transport_attempts=1,
    )
    await planning.close()
    assert client.is_closed
    scripting_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    )
    scripting = OpenRouterScriptingProvider(
        api_key="smoke-test-only",
        model="anthropic/smoke-model",
        prompt_builder=ScriptingPromptBuilder(max_plan_bytes=100_000),
        client=scripting_client,
        max_transport_attempts=1,
    )
    await scripting.close()
    assert scripting_client.is_closed
    print("production OpenRouter installation smoke: OK (zero requests)")


if __name__ == "__main__":
    asyncio.run(smoke())
