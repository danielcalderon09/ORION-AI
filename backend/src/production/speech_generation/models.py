"""Strict immutable contracts for durable speech generation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path

SUPPORTED_SPEECH_MANIFEST_VERSIONS = frozenset({"1.0.0"})
SUPPORTED_SPEECH_ASSET_VERSIONS = frozenset({"1.0.0"})


class SpeechSegmentStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    STORED = "stored"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class SpeechGenerationManifestStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    UNCERTAIN = "uncertain"


class SpeechTimingProvenance(StrEnum):
    SCRIPT_SCENE_ESTIMATE = "script_scene_estimate"
    TEXT_ESTIMATE = "text_estimate"


class SpeechSegmentRequest(ContractModel):
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    source_script_artifact_id: UUID
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str | None = Field(
        default=None,
        pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$",
    )
    sequence_index: int = Field(ge=0, le=49)
    narration_text: str = Field(min_length=1, max_length=6_000, repr=False)
    normalized_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_voice: str = Field(min_length=1, max_length=100)
    requested_language: str = Field(min_length=2, max_length=16)
    requested_speaking_rate: int = Field(ge=60, le=360)
    target_duration_ms: int | None = Field(default=None, ge=1, le=600_000)
    timing_provenance: SpeechTimingProvenance

    @model_validator(mode="after")
    def validate_provenance(self) -> SpeechSegmentRequest:
        if self.shot_id is not None and not self.shot_id.startswith(f"{self.scene_id}-shot-"):
            raise ValueError("speech segment shot must belong to its scene")
        if (
            self.timing_provenance is SpeechTimingProvenance.SCRIPT_SCENE_ESTIMATE
            and self.target_duration_ms is None
        ):
            raise ValueError("script timing provenance requires a target duration")
        return self


class SpeechSegmentAudioMetadata(ContractModel):
    duration_ms: int = Field(gt=0, le=600_000)
    sample_rate_hz: int = Field(ge=8_000, le=48_000)
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    frame_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_duration(self) -> SpeechSegmentAudioMetadata:
        expected = round(self.frame_count * 1_000 / self.sample_rate_hz)
        if abs(expected - self.duration_ms) > 1:
            raise ValueError("speech duration differs from its frame count")
        return self


class SpeechBinaryAssetMetadata(ContractModel):
    source_script_artifact_id: UUID
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=100)
    requested_voice: str = Field(min_length=1, max_length=100)
    requested_language: str = Field(min_length=2, max_length=16)
    deterministic: bool
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def safe_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="speech_asset.metadata.attributes")
        if not isinstance(validated, dict):
            raise ValueError("speech asset attributes must be an object")
        return validated


class SpeechBinaryAsset(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    asset_id: str = Field(pattern=r"^speech-segment-[a-f0-9]{32}$")
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    job_id: UUID
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str | None = Field(
        default=None,
        pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$",
    )
    sequence_index: int = Field(ge=0, le=49)
    mime_type: str = "audio/wav"
    extension: str = "wav"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=50_000_000)
    duration_ms: int = Field(gt=0, le=600_000)
    sample_rate_hz: int = Field(ge=8_000, le=48_000)
    channel_count: int = Field(ge=1, le=2)
    sample_width_bytes: int = Field(ge=1, le=4)
    frame_count: int = Field(gt=0)
    created_at: datetime
    storage_path: str
    metadata: SpeechBinaryAssetMetadata

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech asset creation time must be timezone-aware")
        return value

    @field_validator("storage_path")
    @classmethod
    def safe_storage_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("speech asset path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def validate_asset(self) -> SpeechBinaryAsset:
        if self.schema_version not in SUPPORTED_SPEECH_ASSET_VERSIONS:
            raise ValueError("speech asset schema version is unsupported")
        if self.asset_id != f"speech-{self.segment_id}":
            raise ValueError("speech asset identity differs from segment identity")
        expected = f"production/{self.job_id}/assets/speech/{self.asset_id}.wav"
        if self.storage_path != expected:
            raise ValueError("speech asset path is not contractual")
        if self.mime_type != "audio/wav" or self.extension != "wav":
            raise ValueError("speech asset must be WAV")
        if self.channel_count != 1 or self.sample_width_bytes != 2:
            raise ValueError("speech asset must use mono 16-bit PCM")
        if self.shot_id is not None and not self.shot_id.startswith(f"{self.scene_id}-shot-"):
            raise ValueError("speech asset shot must belong to its scene")
        return self


class SpeechAudioWriteRequest(ContractModel):
    job_id: UUID
    segment: SpeechSegmentRequest
    expected: SpeechSegmentAudioMetadata
    metadata: SpeechBinaryAssetMetadata

    @property
    def asset_id(self) -> str:
        return f"speech-{self.segment.segment_id}"


class ReadSpeechBinaryAsset(ContractModel):
    asset: SpeechBinaryAsset
    content: bytes = Field(repr=False, exclude=True)


class SpeechSegmentManifestEntry(ContractModel):
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    sequence_index: int = Field(ge=0, le=49)
    source_scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    source_shot_id: str | None = Field(
        default=None,
        pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$",
    )
    narration_text: str = Field(min_length=1, max_length=6_000, repr=False)
    normalized_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_duration_ms: int | None = Field(default=None, ge=1, le=600_000)
    timing_provenance: SpeechTimingProvenance
    status: SpeechSegmentStatus
    audio_binary_asset_id: str | None = None
    audio_artifact_id: UUID | None = None
    storage_path: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    sample_rate_hz: int | None = Field(default=None, gt=0)
    channel_count: int | None = Field(default=None, gt=0)
    sample_width_bytes: int | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, gt=0)
    provider: str | None = None
    generation_attempt_count: int = Field(default=0, ge=0)
    generation_started_at: datetime | None = None
    created_at: datetime | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generation_started_at", "created_at")
    @classmethod
    def aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("speech entry timestamps must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="speech_entry.metadata")
        if not isinstance(validated, dict):
            raise ValueError("speech entry metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_entry(self) -> SpeechSegmentManifestEntry:
        durable = (
            self.audio_binary_asset_id,
            self.audio_artifact_id,
            self.storage_path,
            self.mime_type,
            self.extension,
            self.sha256,
            self.size_bytes,
            self.duration_ms,
            self.sample_rate_hz,
            self.channel_count,
            self.sample_width_bytes,
            self.frame_count,
            self.provider,
            self.created_at,
        )
        if self.status is SpeechSegmentStatus.STORED:
            if any(value is None for value in durable):
                raise ValueError("stored speech entry requires complete durable metadata")
            if self.mime_type != "audio/wav" or self.extension != "wav":
                raise ValueError("stored speech entry must be WAV")
            if self.error_code is not None:
                raise ValueError("stored speech entry cannot contain an error")
        elif any(value is not None for value in durable):
            raise ValueError("non-stored speech entry cannot claim durable audio")
        if self.status in {SpeechSegmentStatus.FAILED, SpeechSegmentStatus.UNCERTAIN}:
            if self.error_code is None:
                raise ValueError("failed or uncertain speech entry requires an error code")
        elif self.error_code is not None:
            raise ValueError("active speech entry cannot contain an error")
        if self.status is SpeechSegmentStatus.GENERATING and self.generation_started_at is None:
            raise ValueError("generating speech entry requires a start time")
        return self


class SpeechGenerationSummary(ContractModel):
    total: int = Field(ge=0)
    stored: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    uncertain: int = Field(ge=0)
    total_duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> SpeechGenerationSummary:
        if self.stored + self.pending + self.failed + self.uncertain != self.total:
            raise ValueError("speech summary counts must equal total")
        return self


class SpeechGenerationManifest(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    job_id: UUID
    attempt_number: int = Field(ge=1)
    source_script_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    source_script_artifact_id: UUID
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=100)
    requested_voice: str = Field(min_length=1, max_length=100)
    requested_language: str = Field(min_length=2, max_length=16)
    requested_speaking_rate: int = Field(ge=60, le=360)
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: tuple[SpeechSegmentManifestEntry, ...] = Field(min_length=1, max_length=50)
    summary: SpeechGenerationSummary
    status: SpeechGenerationManifestStatus
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech manifest timestamps must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="speech_manifest.metadata")
        if not isinstance(validated, dict):
            raise ValueError("speech manifest metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_manifest(self) -> SpeechGenerationManifest:
        if self.schema_version not in SUPPORTED_SPEECH_MANIFEST_VERSIONS:
            raise ValueError("speech manifest version is unsupported")
        if self.updated_at < self.created_at:
            raise ValueError("speech manifest update time precedes creation")
        ids = tuple(entry.segment_id for entry in self.entries)
        indexes = tuple(entry.sequence_index for entry in self.entries)
        if len(ids) != len(set(ids)) or len(indexes) != len(set(indexes)):
            raise ValueError("speech manifest entries must be unique")
        if indexes != tuple(range(len(self.entries))):
            raise ValueError("speech manifest entries must use deterministic ordering")
        if self.summary != summarize_speech_entries(self.entries):
            raise ValueError("speech manifest summary does not match entries")
        if self.status is SpeechGenerationManifestStatus.COMPLETED and any(
            entry.status is not SpeechSegmentStatus.STORED for entry in self.entries
        ):
            raise ValueError("completed speech manifest requires all entries stored")
        if self.status is SpeechGenerationManifestStatus.UNCERTAIN and not any(
            entry.status is SpeechSegmentStatus.UNCERTAIN for entry in self.entries
        ):
            raise ValueError("uncertain speech manifest requires an uncertain entry")
        if self.status is SpeechGenerationManifestStatus.FAILED and not any(
            entry.status is SpeechSegmentStatus.FAILED for entry in self.entries
        ):
            raise ValueError("failed speech manifest requires a failed entry")
        if self.status is SpeechGenerationManifestStatus.PARTIAL and not (
            self.summary.stored and self.summary.stored < self.summary.total
        ):
            raise ValueError("partial speech manifest requires incomplete stored output")
        return self


def summarize_speech_entries(
    entries: tuple[SpeechSegmentManifestEntry, ...],
) -> SpeechGenerationSummary:
    return SpeechGenerationSummary(
        total=len(entries),
        stored=sum(entry.status is SpeechSegmentStatus.STORED for entry in entries),
        pending=sum(
            entry.status in {SpeechSegmentStatus.PENDING, SpeechSegmentStatus.GENERATING}
            for entry in entries
        ),
        failed=sum(entry.status is SpeechSegmentStatus.FAILED for entry in entries),
        uncertain=sum(entry.status is SpeechSegmentStatus.UNCERTAIN for entry in entries),
        total_duration_ms=sum(entry.duration_ms or 0 for entry in entries),
    )


def replace_speech_entry(
    manifest: SpeechGenerationManifest,
    entry: SpeechSegmentManifestEntry,
    *,
    status: SpeechGenerationManifestStatus | None = None,
    updated_at: datetime,
) -> SpeechGenerationManifest:
    entries = tuple(
        entry if current.segment_id == entry.segment_id else current for current in manifest.entries
    )
    return manifest.model_copy(
        update={
            "entries": entries,
            "summary": summarize_speech_entries(entries),
            "status": status or manifest.status,
            "updated_at": updated_at,
        }
    )


_ALLOWED_TRANSITIONS = {
    SpeechSegmentStatus.PENDING: {
        SpeechSegmentStatus.PENDING,
        SpeechSegmentStatus.GENERATING,
    },
    SpeechSegmentStatus.GENERATING: {
        SpeechSegmentStatus.GENERATING,
        SpeechSegmentStatus.STORED,
        SpeechSegmentStatus.FAILED,
        SpeechSegmentStatus.UNCERTAIN,
    },
    SpeechSegmentStatus.STORED: {SpeechSegmentStatus.STORED},
    SpeechSegmentStatus.FAILED: {
        SpeechSegmentStatus.FAILED,
        SpeechSegmentStatus.GENERATING,
    },
    SpeechSegmentStatus.UNCERTAIN: {
        SpeechSegmentStatus.UNCERTAIN,
        SpeechSegmentStatus.GENERATING,
        SpeechSegmentStatus.STORED,
    },
}


def validate_speech_manifest_transition(
    previous: SpeechGenerationManifest,
    current: SpeechGenerationManifest,
) -> None:
    immutable_previous = (
        previous.schema_version,
        previous.job_id,
        previous.attempt_number,
        previous.source_script_schema_version,
        previous.source_script_artifact_id,
        previous.source_script_sha256,
        previous.provider,
        previous.requested_voice,
        previous.requested_language,
        previous.requested_speaking_rate,
        previous.configuration_fingerprint,
        previous.created_at,
        tuple(
            (
                entry.segment_id,
                entry.sequence_index,
                entry.source_scene_id,
                entry.source_shot_id,
                entry.narration_text,
                entry.normalized_text_hash,
                entry.target_duration_ms,
                entry.timing_provenance,
            )
            for entry in previous.entries
        ),
    )
    immutable_current = (
        current.schema_version,
        current.job_id,
        current.attempt_number,
        current.source_script_schema_version,
        current.source_script_artifact_id,
        current.source_script_sha256,
        current.provider,
        current.requested_voice,
        current.requested_language,
        current.requested_speaking_rate,
        current.configuration_fingerprint,
        current.created_at,
        tuple(
            (
                entry.segment_id,
                entry.sequence_index,
                entry.source_scene_id,
                entry.source_shot_id,
                entry.narration_text,
                entry.normalized_text_hash,
                entry.target_duration_ms,
                entry.timing_provenance,
            )
            for entry in current.entries
        ),
    )
    if immutable_previous != immutable_current:
        raise ValueError("speech manifest immutable fields changed")
    if current.updated_at < previous.updated_at:
        raise ValueError("speech manifest update time moved backward")
    allowed_manifest = {
        SpeechGenerationManifestStatus.IN_PROGRESS: {
            SpeechGenerationManifestStatus.IN_PROGRESS,
            SpeechGenerationManifestStatus.COMPLETED,
            SpeechGenerationManifestStatus.FAILED,
            SpeechGenerationManifestStatus.PARTIAL,
            SpeechGenerationManifestStatus.UNCERTAIN,
        },
        SpeechGenerationManifestStatus.COMPLETED: {SpeechGenerationManifestStatus.COMPLETED},
        SpeechGenerationManifestStatus.FAILED: {
            SpeechGenerationManifestStatus.FAILED,
            SpeechGenerationManifestStatus.IN_PROGRESS,
            SpeechGenerationManifestStatus.PARTIAL,
            SpeechGenerationManifestStatus.COMPLETED,
        },
        SpeechGenerationManifestStatus.PARTIAL: {
            SpeechGenerationManifestStatus.PARTIAL,
            SpeechGenerationManifestStatus.IN_PROGRESS,
            SpeechGenerationManifestStatus.FAILED,
            SpeechGenerationManifestStatus.COMPLETED,
            SpeechGenerationManifestStatus.UNCERTAIN,
        },
        SpeechGenerationManifestStatus.UNCERTAIN: {
            SpeechGenerationManifestStatus.UNCERTAIN,
            SpeechGenerationManifestStatus.IN_PROGRESS,
            SpeechGenerationManifestStatus.PARTIAL,
            SpeechGenerationManifestStatus.COMPLETED,
        },
    }
    if current.status not in allowed_manifest[previous.status]:
        raise ValueError(
            f"invalid speech manifest transition: {previous.status.value} -> {current.status.value}"
        )
    for before, after in zip(previous.entries, current.entries, strict=True):
        if after.status not in _ALLOWED_TRANSITIONS[before.status]:
            raise ValueError(
                f"invalid speech transition: {before.status.value} -> {after.status.value}"
            )
