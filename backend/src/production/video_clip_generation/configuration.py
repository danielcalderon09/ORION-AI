"""Private global configuration for simulated video clip generation."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel


class VideoClipGenerationConfiguration(ContractModel):
    provider: Literal["simulated"] = "simulated"
    model: str = Field(default="simulated-video-v1", min_length=1, max_length=100)
    output_format: Literal["mp4"] = "mp4"
    codec: Literal["h264"] = "h264"
    frame_rate: Literal[24, 30] = 24
    duration_seconds: float = Field(default=4, gt=0, le=10)
    max_duration_seconds: float = Field(default=10, gt=0, le=10)

    @model_validator(mode="after")
    def validate_duration(self) -> "VideoClipGenerationConfiguration":
        if self.duration_seconds > self.max_duration_seconds:
            raise ValueError("video clip duration exceeds configured maximum")
        return self

    def fingerprint(self) -> str:
        content = json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()
