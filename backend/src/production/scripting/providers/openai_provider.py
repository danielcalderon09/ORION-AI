"""Deprecated import compatibility for the OpenRouter scripting adapter."""

from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)


class OpenAIScriptingProvider(OpenRouterScriptingProvider):
    """Deprecated alias; select ``openrouter`` in runtime settings."""

    __slots__ = ()
