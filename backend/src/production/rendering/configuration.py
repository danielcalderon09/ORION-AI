"""Closed local render-preparation configuration."""

from typing import Literal

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.rendering.models import RendererKind


class RenderingConfiguration(ContractModel):
    renderer: Literal[RendererKind.DRY_RUN] = RendererKind.DRY_RUN
    output_container: Literal["mp4"] = "mp4"
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    max_request_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_manifest_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
