"""Closed local renderer configuration."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.rendering.models import RendererKind


class RenderingConfiguration(ContractModel):
    renderer: RendererKind = RendererKind.DRY_RUN
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    output_container: Literal["mp4"] = "mp4"
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    video_preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "medium"
    video_crf: int = Field(default=20, ge=0, le=35)
    audio_bitrate: Literal["96k", "128k", "160k", "192k", "256k", "320k"] = "192k"
    process_timeout_seconds: int = Field(default=1_800, ge=1, le=7_200)
    probe_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_stderr_bytes: int = Field(default=1_000_000, ge=1_024, le=4_000_000)
    max_output_bytes: int = Field(
        default=2_000_000_000,
        ge=1_024,
        le=4_000_000_000,
    )
    duration_tolerance_ms: int = Field(default=500, ge=0, le=5_000)
    frame_rate_tolerance: float = Field(default=0.01, ge=0, le=1)
    max_request_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_manifest_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_execution_plan_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)

    @field_validator("renderer")
    @classmethod
    def active_renderer_is_implemented(cls, value: RendererKind) -> RendererKind:
        if value is RendererKind.DAVINCI_RESOLVE:
            raise ValueError("davinci_resolve is not an active renderer")
        return value
