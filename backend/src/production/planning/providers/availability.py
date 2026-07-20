"""Lazy availability checks for the optional OpenRouter planning adapter."""

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import cast

from backend.src.production.planning.exceptions import PlanningProviderDependencyError
from backend.src.production.planning.ports import PlanningProvider

OPENROUTER_EXTRA_NAME = "production-llm"
OPENROUTER_DEPENDENCY_MESSAGE = (
    "OpenRouter planning support is not installed. Install the production-llm extra."
)

ModuleImporter = Callable[[str], ModuleType]
PlanningProviderFactory = Callable[..., PlanningProvider]


def load_openrouter_planning_provider(
    *,
    importer: ModuleImporter = import_module,
) -> PlanningProviderFactory:
    """Load the real adapter only after it has been explicitly selected."""

    try:
        module = importer(
            "backend.src.production.planning.providers.openrouter_provider"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise PlanningProviderDependencyError(OPENROUTER_DEPENDENCY_MESSAGE) from None
        raise
    provider = getattr(module, "OpenRouterPlanningProvider", None)
    if not callable(provider):
        raise PlanningProviderDependencyError(OPENROUTER_DEPENDENCY_MESSAGE)
    return cast(PlanningProviderFactory, provider)


# Import compatibility only; runtime selection uses ``openrouter``.
load_openai_planning_provider = load_openrouter_planning_provider
OPENAI_EXTRA_NAME = OPENROUTER_EXTRA_NAME
OPENAI_DEPENDENCY_MESSAGE = OPENROUTER_DEPENDENCY_MESSAGE
