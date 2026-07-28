"""Bounded offline configuration for deterministic audio design."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel


class AudioDesignConfiguration(ContractModel):
    music_provider: Literal["simulated"] = "simulated"
    sound_effect_provider: Literal["simulated"] = "simulated"
    sample_rate_hz: Literal[24_000] = 24_000
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    min_music_duration_ms: int = Field(default=1_000, ge=250, le=10_000)
    max_music_duration_ms: int = Field(default=180_000, ge=1_000, le=600_000)
    min_sound_effect_duration_ms: int = Field(default=50, ge=20, le=1_000)
    max_sound_effect_duration_ms: int = Field(default=5_000, ge=100, le=30_000)
    max_audio_bytes: int = Field(default=10_000_000, ge=1_024, le=50_000_000)
    max_manifest_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_script_bytes: int = Field(default=2_000_000, ge=1_024, le=50_000_000)
    generating_stale_after_seconds: float = Field(default=30, ge=1, le=3_600)

    @model_validator(mode="after")
    def validate_limits(self) -> "AudioDesignConfiguration":
        if self.min_music_duration_ms > self.max_music_duration_ms:
            raise ValueError("minimum music duration exceeds maximum")
        if self.min_sound_effect_duration_ms > self.max_sound_effect_duration_ms:
            raise ValueError("minimum sound-effect duration exceeds maximum")
        maximum_frames = (self.max_music_duration_ms * self.sample_rate_hz + 500) // 1_000
        required_bytes = 44 + maximum_frames * self.channel_count * self.sample_width_bytes
        if required_bytes > self.max_audio_bytes:
            raise ValueError("audio limit cannot hold maximum configured music")
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
