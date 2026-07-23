"""Lazy loading for the optional OpenRouter visual asset planning adapter."""

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import cast

from backend.src.production.visual_asset_planning.exceptions import (
    VisualAssetPlanningProviderDependencyException,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProvider,
)

OPENROUTER_EXTRA_NAME = "production-llm"
OPENROUTER_DEPENDENCY_MESSAGE = (
    "OpenRouter visual asset planning support is not installed. Install the production-llm extra."
)
ModuleImporter = Callable[[str], ModuleType]
VisualAssetPlanningProviderFactory = Callable[..., VisualAssetPlanningProvider]


def load_openrouter_visual_asset_planning_provider(
    *,
    importer: ModuleImporter = import_module,
) -> VisualAssetPlanningProviderFactory:
    try:
        module = importer(
            "backend.src.production.visual_asset_planning.providers.openrouter_provider"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise VisualAssetPlanningProviderDependencyException(
                OPENROUTER_DEPENDENCY_MESSAGE
            ) from None
        raise
    provider = getattr(module, "OpenRouterVisualAssetPlanningProvider", None)
    if not callable(provider):
        raise VisualAssetPlanningProviderDependencyException(OPENROUTER_DEPENDENCY_MESSAGE)
    return cast(VisualAssetPlanningProviderFactory, provider)
