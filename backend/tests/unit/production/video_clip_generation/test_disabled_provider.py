"""Offline safety tests for the image-only video boundary."""

import pytest

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipProviderDependencyException,
)
from backend.src.production.video_clip_generation.providers import (
    DisabledVideoClipGenerationProvider,
)


@pytest.mark.asyncio
async def test_disabled_provider_fails_closed_if_called() -> None:
    provider = DisabledVideoClipGenerationProvider()
    with pytest.raises(
        VideoClipProviderDependencyException,
        match="disabled for image_only strategy",
    ):
        await provider.generate_clip(None)  # type: ignore[arg-type]
