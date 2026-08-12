"""Fail-closed provider boundary for strategies that cannot generate video."""

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipProviderDependencyException,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipProviderRequest,
    VideoClipProviderResponse,
)


class DisabledVideoClipGenerationProvider:
    """Prevent accidental video generation for an explicitly image-only job."""

    async def generate_clip(
        self, request: VideoClipProviderRequest
    ) -> VideoClipProviderResponse:
        raise VideoClipProviderDependencyException(
            "video provider is disabled for image_only strategy"
        )

    async def close(self) -> None:
        return None
