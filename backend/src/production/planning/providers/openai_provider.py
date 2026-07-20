"""Deprecated import compatibility for the OpenRouter planning adapter."""

from backend.src.production.planning.providers.openrouter_provider import (
    OpenRouterPlanningProvider,
)


class OpenAIPlanningProvider(OpenRouterPlanningProvider):
    """Deprecated alias; select ``openrouter`` in runtime settings."""

    __slots__ = ()
