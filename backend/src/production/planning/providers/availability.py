"""Lazy, explicit availability checks for optional planning providers."""

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import cast

from backend.src.production.planning.exceptions import PlanningProviderDependencyError
from backend.src.production.planning.ports import PlanningProvider

OPENAI_EXTRA_NAME = "planning-openai"
OPENAI_DEPENDENCY_MESSAGE = (
    "OpenAI planning support is not installed. Install the planning-openai extra."
)

ModuleImporter = Callable[[str], ModuleType]
PlanningProviderFactory = Callable[..., PlanningProvider]


def load_openai_planning_provider(
    *,
    importer: ModuleImporter = import_module,
) -> PlanningProviderFactory:
    """Load the real adapter only after it has been explicitly selected."""

    try:
        module = importer(
            "backend.src.production.planning.providers.openai_provider"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise PlanningProviderDependencyError(OPENAI_DEPENDENCY_MESSAGE) from None
        raise
    provider = getattr(module, "OpenAIPlanningProvider", None)
    if not callable(provider):
        raise PlanningProviderDependencyError(OPENAI_DEPENDENCY_MESSAGE)
    return cast(PlanningProviderFactory, provider)
