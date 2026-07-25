"""Lazy provider/dependency availability without startup I/O."""

import importlib
import shutil
from collections.abc import Callable
from typing import Any, cast

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipProviderDependencyException,
)


def resolve_media_executable(configured: str | None, default: str) -> str:
    """Return configuration verbatim or a PATH-resolved executable name."""

    if configured is not None and configured.strip():
        return configured.strip()
    return shutil.which(default) or default


def load_openrouter_video_provider() -> Callable[..., Any]:
    try:
        module = importlib.import_module(
            "backend.src.production.video_clip_generation.providers.openrouter_provider"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise VideoClipProviderDependencyException(
                "OpenRouter video support requires the production-llm extra"
            ) from exc
        raise
    return cast(Callable[..., Any], module.OpenRouterVideoClipGenerationProvider)
