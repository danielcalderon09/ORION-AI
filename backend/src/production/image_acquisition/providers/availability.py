"""Lazy OpenRouter image provider availability."""

from importlib import import_module
from typing import Any, Protocol, cast

from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderDependencyException,
)
from backend.src.production.image_acquisition.ports import ImageAcquisitionProvider


class ImageAcquisitionProviderFactory(Protocol):
    def __call__(self, **kwargs: Any) -> ImageAcquisitionProvider: ...


def load_openrouter_image_acquisition_provider() -> ImageAcquisitionProviderFactory:
    try:
        module = import_module(
            "backend.src.production.image_acquisition.providers.openrouter_provider"
        )
    except ImportError as exc:
        raise ImageAcquisitionProviderDependencyException(
            "OpenRouter image acquisition requires the production-llm extra"
        ) from exc
    provider = getattr(module, "OpenRouterImageAcquisitionProvider", None)
    if provider is None:
        raise ImageAcquisitionProviderDependencyException(
            "OpenRouter image acquisition provider is unavailable"
        )
    return cast(ImageAcquisitionProviderFactory, provider)
