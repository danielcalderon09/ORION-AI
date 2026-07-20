"""No-network scripting provider exported without optional HTTP imports."""

from backend.src.production.scripting.providers.simulated_provider import (
    SimulatedScriptingProvider,
)

__all__ = ["SimulatedScriptingProvider"]
