"""Speech-generation provider adapters."""

from backend.src.production.speech_generation.providers.openrouter_provider import (
    OpenRouterSpeechGenerationProvider,
)
from backend.src.production.speech_generation.providers.simulated_provider import (
    SimulatedSpeechGenerationProvider,
)

__all__ = ["OpenRouterSpeechGenerationProvider", "SimulatedSpeechGenerationProvider"]
