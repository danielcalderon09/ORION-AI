"""Canonical SHA-256 identity for a future billable speech request."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechAudioFormat,
    SpeechRemoteGenerationMode,
)


class SpeechRemoteRequestFingerprintInput(ContractModel):
    source_script_artifact_id: UUID
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    normalized_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    voice: str = Field(min_length=1, max_length=200)
    language: str = Field(min_length=2, max_length=32)
    speaking_rate: Decimal | None = Field(default=None, gt=0, le=4)
    audio_format: SpeechAudioFormat
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channel_count: int = Field(ge=1, le=8)
    capability_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    pricing_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation_mode: SpeechRemoteGenerationMode
    options: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator("speaking_rate", mode="before")
    @classmethod
    def reject_float_rate(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech request speaking rate must not use float")
        return value

    @field_validator("options")
    @classmethod
    def safe_options(cls, value: dict[str, bool | int | str]) -> dict[str, bool | int | str]:
        checked = validate_safe_json(value, path="speech_request_fingerprint.options")
        if not isinstance(checked, dict):
            raise ValueError("speech fingerprint options must be an object")
        return checked


def speech_remote_request_fingerprint(
    value: SpeechRemoteRequestFingerprintInput,
) -> str:
    raw = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
