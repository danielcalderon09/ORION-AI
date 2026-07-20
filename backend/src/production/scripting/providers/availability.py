"""Lazy availability checks for the optional OpenRouter scripting adapter."""

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import cast

from backend.src.production.scripting.exceptions import ScriptingProviderDependencyError
from backend.src.production.scripting.ports import ScriptingProvider

OPENROUTER_EXTRA_NAME = "production-llm"
OPENROUTER_DEPENDENCY_MESSAGE = (
    "OpenRouter scripting support is not installed. Install the production-llm extra."
)
ModuleImporter = Callable[[str], ModuleType]
ScriptingProviderFactory = Callable[..., ScriptingProvider]


def load_openrouter_scripting_provider(
    *, importer: ModuleImporter = import_module
) -> ScriptingProviderFactory:
    try:
        module = importer("backend.src.production.scripting.providers.openrouter_provider")
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise ScriptingProviderDependencyError(OPENROUTER_DEPENDENCY_MESSAGE) from None
        raise
    provider = getattr(module, "OpenRouterScriptingProvider", None)
    if not callable(provider):
        raise ScriptingProviderDependencyError(OPENROUTER_DEPENDENCY_MESSAGE)
    return cast(ScriptingProviderFactory, provider)


# Import compatibility only; runtime selection uses ``openrouter``.
load_openai_scripting_provider = load_openrouter_scripting_provider
OPENAI_EXTRA_NAME = OPENROUTER_EXTRA_NAME
OPENAI_DEPENDENCY_MESSAGE = OPENROUTER_DEPENDENCY_MESSAGE
