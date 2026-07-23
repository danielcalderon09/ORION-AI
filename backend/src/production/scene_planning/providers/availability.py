"""Lazy loading for the optional OpenRouter scene-planning adapter."""

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import cast

from backend.src.production.scene_planning.exceptions import (
    ScenePlanningProviderDependencyException,
)
from backend.src.production.scene_planning.ports import ScenePlanningProvider

OPENROUTER_EXTRA_NAME = "production-llm"
OPENROUTER_DEPENDENCY_MESSAGE = (
    "OpenRouter scene-planning support is not installed. Install the production-llm extra."
)
ModuleImporter = Callable[[str], ModuleType]
ScenePlanningProviderFactory = Callable[..., ScenePlanningProvider]


def load_openrouter_scene_planning_provider(
    *,
    importer: ModuleImporter = import_module,
) -> ScenePlanningProviderFactory:
    try:
        module = importer(
            "backend.src.production.scene_planning.providers.openrouter_provider"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise ScenePlanningProviderDependencyException(
                OPENROUTER_DEPENDENCY_MESSAGE
            ) from None
        raise
    provider = getattr(module, "OpenRouterScenePlanningProvider", None)
    if not callable(provider):
        raise ScenePlanningProviderDependencyException(OPENROUTER_DEPENDENCY_MESSAGE)
    return cast(ScenePlanningProviderFactory, provider)
