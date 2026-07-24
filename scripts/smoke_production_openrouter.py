"""Smoke-check optional OpenRouter support without making a request."""

import asyncio
import tomllib
from pathlib import Path

import httpx

from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.providers.openrouter_provider import (
    OpenRouterImageAcquisitionProvider,
)
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers.openrouter_provider import (
    OpenRouterPlanningProvider,
)
from backend.src.production.scene_planning.prompt_builder import (
    ScenePlanningPromptBuilder,
)
from backend.src.production.scene_planning.providers.openrouter_provider import (
    OpenRouterScenePlanningProvider,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)
from backend.src.production.video_clip_generation.providers import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.visual_asset_planning.prompt_builder import (
    VisualAssetPlanningPromptBuilder,
)
from backend.src.production.visual_asset_planning.providers.openrouter_provider import (
    OpenRouterVisualAssetPlanningProvider,
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
    scene_planning_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    )
    scene_planning = OpenRouterScenePlanningProvider(
        api_key="smoke-test-only",
        model="google/smoke-model",
        prompt_builder=ScenePlanningPromptBuilder(max_script_bytes=100_000),
        client=scene_planning_client,
        max_transport_attempts=1,
    )
    await scene_planning.close()
    assert scene_planning_client.is_closed
    visual_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    )
    visual = OpenRouterVisualAssetPlanningProvider(
        api_key="smoke-test-only",
        model="qwen/smoke-model",
        prompt_builder=VisualAssetPlanningPromptBuilder(
            max_scene_plan_bytes=100_000
        ),
        client=visual_client,
        max_transport_attempts=1,
    )
    await visual.close()
    assert visual_client.is_closed
    image_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    )
    image = OpenRouterImageAcquisitionProvider(
        api_key="smoke-test-only",
        model="openai/smoke-image-model",
        prompt_builder=ImageGenerationPromptBuilder(),
        client=image_client,
        max_transport_attempts=1,
    )
    await image.close()
    assert image_client.is_closed
    video = SimulatedVideoClipGenerationProvider()
    await video.close()
    print("production OpenRouter installation smoke: OK (zero requests)")


if __name__ == "__main__":
    asyncio.run(smoke())
