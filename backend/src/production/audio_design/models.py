"""Strict immutable contracts for durable simulated audio design."""

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

SUPPORTED_AUDIO_DESIGN_MANIFEST_VERSIONS = frozenset({"1.0.0"})
AUDIO_DESIGN_SCHEMA_VERSION = "1.0.0"


class AudioAssetKind(StrEnum):
    MUSIC = "music"
    SOUND_EFFECT = "sound_effect"


class AudioDesignManifestStatus(StrEnum):
    PREPARED = "prepared"
    GENERATING = "generating"
    COMPLETE = "complete"
    FAILED = "failed"


class AudioDesignAssetStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    STORED = "stored"
    FAILED = "failed"


class MusicMood(StrEnum):
    NEUTRAL = "neutral"
    CALM = "calm"
    HOPEFUL = "hopeful"
    FOCUSED = "focused"
    WARM = "warm"
    SUBDUED = "subdued"


class SoundEffectCueType(StrEnum):
    TRANSITION = "transition"
    IMPACT = "impact"
    RISE = "rise"
    ALERT = "alert"
    AMBIENCE = "ambience"
    WHOOSH = "whoosh"
    SOFT_CLICK = "soft_click"


class MusicRequirement(ContractModel):
    requirement_id: str = Field(pattern=r"^music-[a-f0-9]{24}$")
    enabled: Literal[True] = True
    purpose: Literal["background_bed"] = "background_bed"
    mood: MusicMood = MusicMood.NEUTRAL
    intensity: int = Field(default=30, ge=0, le=100)
    target_duration_ms: int = Field(gt=0, le=600_000)
    loopable: bool = True
    duck_under_narration: bool = True
    requested_format: Literal["wav_pcm"] = "wav_pcm"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="music_requirement.metadata")
        if not isinstance(validated, dict):
            raise ValueError("music requirement metadata must be an object")
        return validated


class SoundEffectRequirement(ContractModel):
    requirement_id: str = Field(pattern=r"^sfx-[a-f0-9]{24}$")
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str | None = Field(
        default=None,
        pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$",
    )
    cue_type: SoundEffectCueType
    description: str = Field(min_length=1, max_length=100)
    target_offset_ms: int = Field(ge=0, le=3_600_000)
    target_duration_ms: int = Field(gt=0, le=30_000)
    intensity: int = Field(default=50, ge=0, le=100)
    requested_format: Literal["wav_pcm"] = "wav_pcm"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="sound_effect_requirement.metadata")
        if not isinstance(validated, dict):
            raise ValueError("sound-effect metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_scene_identity(self) -> SoundEffectRequirement:
        if self.shot_id is not None and not self.shot_id.startswith(f"{self.scene_id}-shot-"):
            raise ValueError("sound-effect shot must belong to its scene")
        if self.description != f"Generic {self.cue_type.value} cue":
            raise ValueError("sound-effect descriptions must use the controlled summary")
        return self


class AudioDesignPlan(ContractModel):
    schema_version: str = AUDIO_DESIGN_SCHEMA_VERSION
    job_id: UUID
    source_script_artifact_id: UUID
    production_script_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    music_requirement: MusicRequirement | None = None
    sound_effect_requirements: tuple[SoundEffectRequirement, ...] = Field(
        default=(),
        max_length=500,
    )
    total_target_duration_ms: int = Field(gt=0, le=3_600_000)
    plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="audio_design_plan.metadata")
        if not isinstance(validated, dict):
            raise ValueError("audio-design plan metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_requirements(self) -> AudioDesignPlan:
        identifiers = [requirement.requirement_id for requirement in self.sound_effect_requirements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sound-effect requirement IDs must be unique")
        offsets = [
            (item.target_offset_ms, item.requirement_id) for item in self.sound_effect_requirements
        ]
        if offsets != sorted(offsets):
            raise ValueError("sound effects must use deterministic timeline ordering")
        return self


class AudioPcmMetadata(ContractModel):
    duration_ms: int = Field(gt=0, le=600_000)
    sample_rate_hz: Literal[24_000] = 24_000
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    frame_count: int = Field(gt=0)
    peak_amplitude: int = Field(ge=1, le=30_000)

    @model_validator(mode="after")
    def validate_duration(self) -> AudioPcmMetadata:
        expected = (self.frame_count * 1_000 + self.sample_rate_hz // 2) // (self.sample_rate_hz)
        if abs(expected - self.duration_ms) > 1:
            raise ValueError("audio duration differs from frame count")
        return self


class AudioFormatExpectation(ContractModel):
    duration_ms: int = Field(gt=0, le=600_000)
    sample_rate_hz: Literal[24_000] = 24_000
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    frame_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_duration(self) -> AudioFormatExpectation:
        expected = (self.frame_count * 1_000 + self.sample_rate_hz // 2) // (self.sample_rate_hz)
        if abs(expected - self.duration_ms) > 1:
            raise ValueError("expected audio duration differs from frame count")
        return self


class MusicGenerationRequest(ContractModel):
    request_id: str = Field(pattern=r"^music-request-[a-f0-9]{24}$")
    requirement_id: str = Field(pattern=r"^music-[a-f0-9]{24}$")
    mood: MusicMood
    intensity: int = Field(ge=0, le=100)
    duration_ms: int = Field(gt=0, le=600_000)
    loopable: bool
    sample_rate_hz: Literal[24_000] = 24_000
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SoundEffectGenerationRequest(ContractModel):
    request_id: str = Field(pattern=r"^sfx-request-[a-f0-9]{24}$")
    requirement_id: str = Field(pattern=r"^sfx-[a-f0-9]{24}$")
    cue_type: SoundEffectCueType
    intensity: int = Field(ge=0, le=100)
    duration_ms: int = Field(gt=0, le=30_000)
    sample_rate_hz: Literal[24_000] = 24_000
    channel_count: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class GeneratedAudioResult(ContractModel):
    provider_id: str = Field(min_length=1, max_length=100)
    provider_asset_id: str = Field(min_length=1, max_length=100)
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    media_type: Literal["audio/wav"] = "audio/wav"
    format: Literal["wav_pcm"] = "wav_pcm"
    audio: AudioPcmMetadata
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    deterministic: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="generated_audio.metadata")
        if not isinstance(validated, dict):
            raise ValueError("generated audio metadata must be an object")
        return validated


class AudioAssetExpectation(ContractModel):
    job_id: UUID
    kind: AudioAssetKind
    requirement_id: str = Field(pattern=r"^(?:music|sfx)-[a-f0-9]{24}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_id: str = Field(min_length=1, max_length=100)
    audio: AudioFormatExpectation


class StoredAudioDesignAsset(ContractModel):
    asset_id: str = Field(pattern=r"^audio-(?:music|sfx)-[a-f0-9]{24}-[a-f0-9]{16}$")
    job_id: UUID
    kind: AudioAssetKind
    requirement_id: str = Field(pattern=r"^(?:music|sfx)-[a-f0-9]{24}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    storage_path: str
    media_type: Literal["audio/wav"] = "audio/wav"
    extension: Literal["wav"] = "wav"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=44, le=50_000_000)
    audio: AudioPcmMetadata
    provider_id: str = Field(min_length=1, max_length=100)

    @field_validator("storage_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("audio-design path must use POSIX separators")
        return normalized


class ReadStoredAudioDesignAsset(ContractModel):
    asset: StoredAudioDesignAsset
    content: bytes = Field(repr=False, exclude=True)


class AudioDesignManifestEntry(ContractModel):
    sequence_index: int = Field(ge=0, le=500)
    kind: AudioAssetKind
    requirement_id: str = Field(pattern=r"^(?:music|sfx)-[a-f0-9]{24}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_id: str = Field(min_length=1, max_length=100)
    expected_audio: AudioFormatExpectation
    status: AudioDesignAssetStatus = AudioDesignAssetStatus.PENDING
    generation_attempt_count: int = Field(default=0, ge=0)
    generation_started_at: datetime | None = None
    stored_at: datetime | None = None
    asset_id: str | None = None
    artifact_id: UUID | None = None
    storage_path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, gt=44)
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generation_started_at", "stored_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("audio-design entry time must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="audio_design_entry.metadata")
        if not isinstance(validated, dict):
            raise ValueError("audio-design entry metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_state(self) -> AudioDesignManifestEntry:
        durable = (
            self.stored_at,
            self.asset_id,
            self.artifact_id,
            self.storage_path,
            self.sha256,
            self.size_bytes,
        )
        if self.status is AudioDesignAssetStatus.STORED:
            if any(value is None for value in durable):
                raise ValueError("stored audio-design entry needs durable metadata")
            if self.error_code is not None or self.generation_started_at is not None:
                raise ValueError("stored entry cannot retain active/error state")
        elif any(value is not None for value in durable):
            raise ValueError("non-stored entry cannot claim durable audio")
        if self.status is AudioDesignAssetStatus.GENERATING and self.generation_started_at is None:
            raise ValueError("generating entry needs a start time")
        if self.status is AudioDesignAssetStatus.FAILED and self.error_code is None:
            raise ValueError("failed entry needs a safe error code")
        if self.status is not AudioDesignAssetStatus.FAILED and self.error_code is not None:
            raise ValueError("only failed entries may contain an error")
        return self


class AudioDesignSummary(ContractModel):
    expected: int = Field(ge=0)
    stored: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    music_assets: int = Field(ge=0, le=1)
    sound_effect_assets: int = Field(ge=0, le=500)
    total_duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> AudioDesignSummary:
        if self.stored + self.pending + self.failed != self.expected:
            raise ValueError("audio-design summary counts differ")
        if self.music_assets + self.sound_effect_assets != self.stored:
            raise ValueError("stored audio-design kind counts differ")
        return self


class AudioDesignManifest(ContractModel):
    schema_version: str = AUDIO_DESIGN_SCHEMA_VERSION
    job_id: UUID
    stage: Literal[ProductionStage.PREPARING_MUSIC] = ProductionStage.PREPARING_MUSIC
    attempt_number: int = Field(ge=1)
    source_script_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    source_script_artifact_id: UUID
    production_script_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    audio_design_plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    music_provider_id: str = Field(min_length=1, max_length=100)
    sound_effect_provider_id: str = Field(min_length=1, max_length=100)
    expected_music_requirement_id: str | None = Field(
        default=None,
        pattern=r"^music-[a-f0-9]{24}$",
    )
    expected_sound_effect_requirement_ids: tuple[str, ...] = Field(
        default=(),
        max_length=500,
    )
    entries: tuple[AudioDesignManifestEntry, ...] = Field(
        default=(),
        max_length=501,
    )
    summary: AudioDesignSummary
    status: AudioDesignManifestStatus
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audio-design manifest time must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="audio_design_manifest.metadata")
        if not isinstance(validated, dict):
            raise ValueError("audio-design manifest metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_manifest(self) -> AudioDesignManifest:
        if self.schema_version not in SUPPORTED_AUDIO_DESIGN_MANIFEST_VERSIONS:
            raise ValueError("audio-design manifest schema is unsupported")
        if self.updated_at < self.created_at:
            raise ValueError("manifest update precedes creation")
        ids = tuple(entry.requirement_id for entry in self.entries)
        if len(ids) != len(set(ids)):
            raise ValueError("audio-design manifest entry IDs must be unique")
        if tuple(entry.sequence_index for entry in self.entries) != tuple(range(len(self.entries))):
            raise ValueError("audio-design entries must be deterministically ordered")
        if self.summary != summarize_audio_design_entries(self.entries):
            raise ValueError("audio-design manifest summary differs")
        music = tuple(
            entry.requirement_id for entry in self.entries if entry.kind is AudioAssetKind.MUSIC
        )
        effects = tuple(
            entry.requirement_id
            for entry in self.entries
            if entry.kind is AudioAssetKind.SOUND_EFFECT
        )
        if music != (
            (self.expected_music_requirement_id,)
            if self.expected_music_requirement_id is not None
            else ()
        ):
            raise ValueError("manifest music requirement differs")
        if effects != self.expected_sound_effect_requirement_ids:
            raise ValueError("manifest SFX requirements differ")
        if self.status is AudioDesignManifestStatus.COMPLETE and any(
            entry.status is not AudioDesignAssetStatus.STORED for entry in self.entries
        ):
            raise ValueError("complete audio-design manifest has incomplete entries")
        if self.status is AudioDesignManifestStatus.FAILED and not any(
            entry.status is AudioDesignAssetStatus.FAILED for entry in self.entries
        ):
            raise ValueError("failed audio-design manifest has no failed entry")
        if self.status is AudioDesignManifestStatus.PREPARED and any(
            entry.status is not AudioDesignAssetStatus.PENDING for entry in self.entries
        ):
            raise ValueError("prepared manifest must contain only pending entries")
        return self


def summarize_audio_design_entries(
    entries: tuple[AudioDesignManifestEntry, ...],
) -> AudioDesignSummary:
    return AudioDesignSummary(
        expected=len(entries),
        stored=sum(entry.status is AudioDesignAssetStatus.STORED for entry in entries),
        pending=sum(
            entry.status in {AudioDesignAssetStatus.PENDING, AudioDesignAssetStatus.GENERATING}
            for entry in entries
        ),
        failed=sum(entry.status is AudioDesignAssetStatus.FAILED for entry in entries),
        music_assets=sum(
            entry.status is AudioDesignAssetStatus.STORED and entry.kind is AudioAssetKind.MUSIC
            for entry in entries
        ),
        sound_effect_assets=sum(
            entry.status is AudioDesignAssetStatus.STORED
            and entry.kind is AudioAssetKind.SOUND_EFFECT
            for entry in entries
        ),
        total_duration_ms=sum(
            entry.expected_audio.duration_ms
            for entry in entries
            if entry.status is AudioDesignAssetStatus.STORED
        ),
    )


def replace_audio_design_entry(
    manifest: AudioDesignManifest,
    entry: AudioDesignManifestEntry,
    *,
    status: AudioDesignManifestStatus,
    updated_at: datetime,
) -> AudioDesignManifest:
    entries = tuple(
        entry if existing.requirement_id == entry.requirement_id else existing
        for existing in manifest.entries
    )
    if entries == manifest.entries:
        raise ValueError("audio-design manifest entry was not found")
    return manifest.model_copy(
        update={
            "entries": entries,
            "summary": summarize_audio_design_entries(entries),
            "status": status,
            "updated_at": updated_at,
        }
    )
