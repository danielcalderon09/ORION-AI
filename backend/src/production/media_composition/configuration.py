"""Bounded, renderer-neutral media composition settings."""

from typing import Literal

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel


class MediaCompositionConfiguration(ContractModel):
    max_source_manifest_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_plan_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_manifest_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    color_space: Literal["rec709"] = "rec709"
    title_safe_percent: int = Field(default=80, ge=50, le=100)
    action_safe_percent: int = Field(default=90, ge=50, le=100)
    music_gain_db: int = Field(default=-18, ge=-60, le=0)
    music_duck_gain_db: int = Field(default=-30, ge=-60, le=0)
    narration_gain_db: int = Field(default=0, ge=-12, le=12)
    sound_effect_gain_db: int = Field(default=-6, ge=-60, le=12)
    fade_duration_ms: int = Field(default=250, ge=0, le=5_000)
    crossfade_duration_ms: int = Field(default=250, ge=0, le=5_000)

    @model_validator(mode="after")
    def validate_relationships(self) -> "MediaCompositionConfiguration":
        if self.title_safe_percent > self.action_safe_percent:
            raise ValueError("title safe area cannot exceed action safe area")
        if self.music_duck_gain_db > self.music_gain_db:
            raise ValueError("ducked music gain must not exceed base music gain")
        return self
