"""Image acquisition provider adapters."""

from backend.src.production.image_acquisition.providers.simulated_provider import (
    SimulatedImageAcquisitionProvider,
)

__all__ = ["SimulatedImageAcquisitionProvider"]
