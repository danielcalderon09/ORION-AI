"""Strict contracts for definitive acceptance of a rendered MP4."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.domain.path_rules import validate_relative_path

FINAL_RENDER_VALIDATION_SCHEMA_VERSION = "1.0.0"


class FinalValidationStatus(StrEnum):
    PREPARED = "prepared"
    VALIDATING = "validating"
    VALIDATED = "validated"
    FAILED = "failed"


class FinalValidationResult(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class FinalFFprobeSummary(ContractModel):
    duration_ms: int = Field(gt=0)
    duration_frames: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate_numerator: int = Field(gt=0)
    frame_rate_denominator: int = Field(gt=0)
    video_codec: str = Field(min_length=1, max_length=30)
    audio_codec: str | None = Field(default=None, max_length=30)
    pixel_format: str = Field(min_length=1, max_length=30)
    video_stream_count: int = Field(ge=0, le=8)
    audio_stream_count: int = Field(ge=0, le=8)
    subtitle_stream_count: int = Field(ge=0, le=8)
    format_names: tuple[str, ...] = Field(min_length=1, max_length=20)
    probe_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class FinalRenderFingerprints(ContractModel):
    request_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    plan_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    timeline_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    execution_plan_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    render_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    probe_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class FinalRenderValidationManifest(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    stage: Literal[ProductionStage.VALIDATING_RENDER] = ProductionStage.VALIDATING_RENDER
    attempt_number: int = Field(ge=1)
    status: FinalValidationStatus
    render_artifact_id: UUID | None = None
    render_relative_path: str | None = None
    render_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    render_size_bytes: int | None = Field(default=None, gt=0)
    render_manifest_artifact_id: UUID | None = None
    media_composition_plan_artifact_id: UUID | None = None
    execution_plan_artifact_id: UUID | None = None
    validation_timestamp: datetime | None = None
    ffprobe_summary: FinalFFprobeSummary | None = None
    validation_result: FinalValidationResult
    warnings: tuple[str, ...] = Field(default=(), max_length=100)
    error_codes: tuple[str, ...] = Field(default=(), max_length=100)
    fingerprints: FinalRenderFingerprints
    plan_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    execution_plan_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    validation_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("render_relative_path")
    @classmethod
    def safe_render_path(cls, value: str | None) -> str | None:
        return validate_relative_path(value) if value is not None else None

    @field_validator("created_at", "updated_at", "validation_timestamp")
    @classmethod
    def aware_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("final-render validation timestamps must be timezone-aware")
        return value

    @field_validator("warnings", "error_codes")
    @classmethod
    def stable_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("final-render validation codes must be unique and sorted")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="final_render_validation.metadata")
        if not isinstance(result, dict):
            raise ValueError("final-render validation metadata must be an object")
        return result

    @model_validator(mode="after")
    def consistent_manifest(self) -> FinalRenderValidationManifest:
        if self.updated_at < self.created_at:
            raise ValueError("final-render validation time moved backward")
        if self.plan_fingerprint != self.fingerprints.plan_fingerprint:
            raise ValueError("final-render plan fingerprint differs")
        if self.execution_plan_fingerprint != (self.fingerprints.execution_plan_fingerprint):
            raise ValueError("final-render execution-plan fingerprint differs")
        if self.status is FinalValidationStatus.VALIDATED:
            required = (
                self.render_artifact_id,
                self.render_relative_path,
                self.render_checksum,
                self.render_size_bytes,
                self.render_manifest_artifact_id,
                self.media_composition_plan_artifact_id,
                self.execution_plan_artifact_id,
                self.validation_timestamp,
                self.ffprobe_summary,
                self.validation_fingerprint,
            )
            if any(item is None for item in required):
                raise ValueError("validated final-render manifest is incomplete")
            if self.validation_result is not FinalValidationResult.PASSED or self.error_codes:
                raise ValueError("validated final-render manifest did not pass")
        elif self.status is FinalValidationStatus.FAILED:
            if (
                self.validation_result is not FinalValidationResult.FAILED
                or not self.error_codes
                or self.validation_timestamp is None
                or self.validation_fingerprint is None
            ):
                raise ValueError("failed final-render manifest needs durable diagnostics")
        elif self.validation_result is not FinalValidationResult.PENDING:
            raise ValueError("non-terminal validation result must remain pending")
        return self
