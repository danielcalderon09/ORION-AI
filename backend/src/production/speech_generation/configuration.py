"""Bounded provider-neutral speech-generation configuration."""

import hashlib
import json
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel


class SpeechGenerationConfiguration(ContractModel):
    provider: Literal["simulated", "openrouter"] = "simulated"
    voice: str = Field(default="simulated-neutral-v1", min_length=1, max_length=100)
    language: str = Field(default="es-ES", min_length=2, max_length=16)
    words_per_minute: int = Field(default=150, ge=60, le=360)
    sample_rate_hz: int = Field(default=24_000, ge=8_000, le=48_000)
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    min_duration_ms: int = Field(default=250, ge=100, le=10_000)
    max_segment_duration_ms: int = Field(default=120_000, ge=250, le=600_000)
    max_audio_bytes: int = Field(default=8_000_000, ge=1_024, le=50_000_000)
    max_manifest_bytes: int = Field(default=4_000_000, ge=1_024, le=16_000_000)
    max_script_bytes: int = Field(default=2_000_000, ge=1_024, le=50_000_000)
    generating_stale_after_seconds: float = Field(default=30, ge=1, le=3_600)

    @model_validator(mode="after")
    def validate_limits(self) -> "SpeechGenerationConfiguration":
        if self.min_duration_ms > self.max_segment_duration_ms:
            raise ValueError("minimum speech duration exceeds maximum segment duration")
        maximum_frames = (self.max_segment_duration_ms * self.sample_rate_hz + 999) // 1_000
        maximum_wav_bytes = 44 + maximum_frames * self.channel_count * self.sample_width_bytes
        if maximum_wav_bytes > self.max_audio_bytes:
            raise ValueError("speech audio limit cannot hold the configured maximum duration")
        return self

    def fingerprint(self) -> str:
        content = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()


class SpeechRemotePreparationConfiguration(ContractModel):
    """Fail-closed settings for a durable remote speech adapter."""

    allow_billable_requests: bool = False
    remote_provider: Literal["disabled", "openrouter"] = "disabled"
    remote_model: str | None = Field(default=None, min_length=1, max_length=300)
    remote_voice: str | None = Field(default=None, min_length=1, max_length=200)
    maximum_estimated_cost: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=18,
        decimal_places=9,
    )
    estimated_cost: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=18,
        decimal_places=9,
    )
    max_poll_attempts: int = Field(default=120, ge=1, le=1000)
    poll_interval_seconds: float = Field(default=5, gt=0, le=300)
    remote_job_max_bytes: int = Field(
        default=1_000_000,
        ge=1_024,
        le=4_000_000,
    )

    @field_validator("maximum_estimated_cost", "estimated_cost", mode="before")
    @classmethod
    def reject_float_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("remote speech maximum cost must not use float")
        return value

    @model_validator(mode="after")
    def fail_closed(self) -> "SpeechRemotePreparationConfiguration":
        if self.remote_provider == "disabled":
            if self.allow_billable_requests:
                raise ValueError("disabled remote speech cannot authorize billing")
            if self.remote_model is not None or self.remote_voice is not None:
                raise ValueError("disabled remote speech cannot select a model or voice")
            if self.maximum_estimated_cost is not None or self.estimated_cost is not None:
                raise ValueError("disabled remote speech cannot authorize a cost")
            return self
        if not self.allow_billable_requests:
            raise ValueError("OpenRouter speech requires explicit billable authorization")
        if self.remote_model is None or self.remote_voice is None:
            raise ValueError("OpenRouter speech requires an explicit model and voice")
        if self.maximum_estimated_cost is None or self.estimated_cost is None:
            raise ValueError("OpenRouter speech requires explicit cost authorization")
        if self.estimated_cost > self.maximum_estimated_cost:
            raise ValueError("speech estimate exceeds authorized cost")
        return self
