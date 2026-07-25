"""Private global configuration for provider-neutral video clip generation."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel


class VideoClipGenerationConfiguration(ContractModel):
    provider: Literal["simulated", "openrouter"] = "simulated"
    model: str = Field(default="simulated-video-v1", min_length=1, max_length=300)
    output_format: Literal["mp4"] = "mp4"
    codec: Literal["h264"] = "h264"
    resolution: Literal["720p", "1080p"] = "720p"
    generate_audio: Literal[False] = False
    frame_rate: Literal[24, 30] = 24
    duration_seconds: float = Field(default=4, gt=0, le=10)
    max_duration_seconds: float = Field(default=10, gt=0, le=10)

    @model_validator(mode="after")
    def validate_duration(self) -> "VideoClipGenerationConfiguration":
        if self.duration_seconds > self.max_duration_seconds:
            raise ValueError("video clip duration exceeds configured maximum")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        if self.provider == "simulated":
            # Preserve the exact Phase 5F.1 fingerprint for durable recovery.
            payload.pop("resolution", None)
            payload.pop("generate_audio", None)
        content = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def aspect_ratio(self, source_width: int, source_height: int) -> str:
        ratio = source_width / source_height
        choices = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}
        return min(choices, key=lambda item: abs(choices[item] - ratio))

    def output_dimensions(self, source_width: int, source_height: int) -> tuple[int, int]:
        if self.provider == "simulated":
            return source_width, source_height
        base = 1080 if self.resolution == "1080p" else 720
        aspect = self.aspect_ratio(source_width, source_height)
        if aspect == "16:9":
            return (1920, 1080) if base == 1080 else (1280, 720)
        if aspect == "9:16":
            return (1080, 1920) if base == 1080 else (720, 1280)
        return base, base
