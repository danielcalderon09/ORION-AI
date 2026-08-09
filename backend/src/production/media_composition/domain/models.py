"""Strict renderer-neutral contracts for a complete deterministic timeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.domain.path_rules import validate_relative_path

MEDIA_COMPOSITION_SCHEMA_VERSION = "1.0.0"
SUPPORTED_MEDIA_COMPOSITION_VERSIONS = frozenset({MEDIA_COMPOSITION_SCHEMA_VERSION})


class CompositionAssetKind(StrEnum):
    VIDEO = "video"
    NARRATION = "narration"
    MUSIC = "music"
    SOUND_EFFECT = "sound_effect"
    SUBTITLES = "subtitles"


class CompositionTrackKind(StrEnum):
    VIDEO = "video"
    NARRATION = "narration"
    MUSIC = "music"
    SOUND_EFFECT = "sound_effect"
    SUBTITLES = "subtitles"


class CompositionAssetAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    CORRUPT = "corrupt"


class CompositionManifestStatus(StrEnum):
    PREPARED = "prepared"
    COMPLETE = "complete"
    INVALIDATED = "invalidated"
    FAILED = "failed"


class CompositionIssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class CompositionTransitionKind(StrEnum):
    NONE = "none"
    CUT = "cut"
    DISSOLVE = "dissolve"
    FADE = "fade"
    WIPE = "wipe"
    MATCH_CUT = "match_cut"


class VolumeEnvelopePoint(ContractModel):
    offset_ms: int = Field(ge=0, le=3_600_000)
    gain_db: int = Field(ge=-60, le=12)


class VolumeEnvelope(ContractModel):
    base_gain_db: int = Field(ge=-60, le=12)
    points: tuple[VolumeEnvelopePoint, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def ordered_points(self) -> VolumeEnvelope:
        offsets = tuple(point.offset_ms for point in self.points)
        if offsets != tuple(sorted(offsets)) or len(offsets) != len(set(offsets)):
            raise ValueError("volume envelope points must be unique and ordered")
        return self


class CompositionAssetReference(ContractModel):
    asset_id: str = Field(min_length=1, max_length=200)
    artifact_id: UUID
    kind: CompositionAssetKind
    relative_path: str
    mime_type: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=250_000_000)
    duration_ms: int | None = Field(default=None, gt=0, le=3_600_000)
    width: int | None = Field(default=None, gt=0, le=16_384)
    height: int | None = Field(default=None, gt=0, le=16_384)
    frame_rate: int | None = Field(default=None, gt=0, le=120)
    frame_count: int | None = Field(default=None, gt=0)
    sample_rate_hz: int | None = Field(default=None, gt=0, le=192_000)
    channel_count: int | None = Field(default=None, gt=0, le=8)
    sample_width_bytes: int | None = Field(default=None, gt=0, le=4)
    scene_id: str | None = None
    shot_id: str | None = None

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("asset path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def validate_media_metadata(self) -> CompositionAssetReference:
        video_fields = (self.width, self.height, self.frame_rate, self.frame_count)
        audio_fields = (
            self.sample_rate_hz,
            self.channel_count,
            self.sample_width_bytes,
        )
        if self.kind is CompositionAssetKind.VIDEO and any(item is None for item in video_fields):
            raise ValueError("video asset requires complete frame metadata")
        if self.kind in {
            CompositionAssetKind.NARRATION,
            CompositionAssetKind.MUSIC,
            CompositionAssetKind.SOUND_EFFECT,
        } and any(item is None for item in audio_fields):
            raise ValueError("audio asset requires complete PCM metadata")
        return self


class SourceManifestReference(ContractModel):
    artifact_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=16_000_000)

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("manifest path must use POSIX separators")
        return normalized


class CompositionClip(ContractModel):
    clip_id: str = Field(pattern=r"^clip-[a-z0-9-]{1,180}$")
    kind: CompositionAssetKind
    asset_id: str
    scene_id: str | None = None
    shot_id: str | None = None
    sequence_index: int = Field(ge=0, le=5_000)
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    timeline_start_ms: int = Field(ge=0, le=3_600_000)
    timeline_end_ms: int = Field(gt=0, le=3_600_000)
    source_in_frame: int = Field(default=0, ge=0)
    source_out_frame: int | None = Field(default=None, gt=0)
    playback_mode: Literal["once", "loop"] = "once"
    loop_count: int = Field(default=1, ge=1, le=10_000)
    playback_rate: float = Field(default=1.0, ge=1.0, le=1.05)
    fade_in_ms: int = Field(default=0, ge=0, le=10_000)
    fade_out_ms: int = Field(default=0, ge=0, le=10_000)
    volume_envelope: VolumeEnvelope | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> CompositionClip:
        if self.timeline_end_frame <= self.timeline_start_frame:
            raise ValueError("clip frame interval must be positive")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("clip time interval must be positive")
        if self.source_out_frame is not None and self.source_out_frame <= self.source_in_frame:
            raise ValueError("clip source interval must be positive")
        if self.playback_mode == "once" and self.loop_count != 1:
            raise ValueError("one-shot playback cannot declare multiple loops")
        if self.playback_mode == "loop" and self.loop_count < 2:
            raise ValueError("looped playback requires at least two iterations")
        duration = self.timeline_end_ms - self.timeline_start_ms
        if self.fade_in_ms > duration or self.fade_out_ms > duration:
            raise ValueError("clip fade exceeds clip duration")
        return self


class CompositionTrack(ContractModel):
    track_id: str = Field(pattern=r"^track-[a-z0-9-]{1,80}$")
    kind: CompositionTrackKind
    order: int = Field(ge=0, le=20)
    enabled: bool
    disabled_reason: str | None = Field(default=None, max_length=200)
    clips: tuple[CompositionClip, ...] = Field(default=(), max_length=5_000)

    @model_validator(mode="after")
    def validate_track(self) -> CompositionTrack:
        indexes = tuple(clip.sequence_index for clip in self.clips)
        if indexes != tuple(range(len(self.clips))):
            raise ValueError("track clips must use consecutive sequence indexes")
        if len({clip.clip_id for clip in self.clips}) != len(self.clips):
            raise ValueError("track clip IDs must be unique")
        if not self.enabled and self.clips:
            raise ValueError("disabled track cannot contain clips")
        if self.enabled and self.disabled_reason is not None:
            raise ValueError("enabled track cannot contain a disabled reason")
        if not self.enabled and not self.disabled_reason:
            raise ValueError("disabled track requires a reason")
        return self


class CompositionTransition(ContractModel):
    transition_id: str = Field(pattern=r"^transition-[0-9]{4}$")
    sequence_index: int = Field(ge=0, le=5_000)
    kind: CompositionTransitionKind
    boundary_frame: int = Field(gt=0)
    duration_frames: int = Field(ge=0)
    duration_ms: int = Field(ge=0, le=10_000)
    from_clip_id: str
    to_clip_id: str | None = None

    @model_validator(mode="after")
    def valid_duration(self) -> CompositionTransition:
        instant = {
            CompositionTransitionKind.NONE,
            CompositionTransitionKind.CUT,
            CompositionTransitionKind.MATCH_CUT,
        }
        if self.kind in instant and (self.duration_frames or self.duration_ms):
            raise ValueError("instant transition must have zero duration")
        if self.kind not in instant and not self.duration_frames:
            raise ValueError("timed transition requires frames")
        return self


class DuckingInstruction(ContractModel):
    instruction_id: str = Field(pattern=r"^duck-[0-9]{4}$")
    source_track_id: Literal["track-narration"] = "track-narration"
    target_track_id: Literal["track-music"] = "track-music"
    start_ms: int = Field(ge=0, le=3_600_000)
    end_ms: int = Field(gt=0, le=3_600_000)
    target_gain_db: int = Field(ge=-60, le=0)
    attack_ms: int = Field(ge=0, le=5_000)
    release_ms: int = Field(ge=0, le=5_000)

    @model_validator(mode="after")
    def valid_interval(self) -> DuckingInstruction:
        if self.end_ms <= self.start_ms:
            raise ValueError("ducking interval must be positive")
        return self


class SubtitleCue(ContractModel):
    cue_id: str = Field(pattern=r"^subtitle-[0-9]{4}$")
    sequence_index: int = Field(ge=0, le=10_000)
    asset_id: str
    source_cue_index: int = Field(ge=0, le=10_000)
    start_ms: int = Field(ge=0, le=3_600_000)
    end_ms: int = Field(gt=0, le=3_600_000)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    placement: Literal["bottom_center"] = "bottom_center"
    title_safe: Literal[True] = True

    @model_validator(mode="after")
    def valid_interval(self) -> SubtitleCue:
        if self.end_ms <= self.start_ms:
            raise ValueError("subtitle interval must be positive")
        return self


class OutputVideoSpecification(ContractModel):
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    frame_rate_numerator: int = Field(gt=0, le=120)
    frame_rate_denominator: Literal[1] = 1
    aspect_ratio: str = Field(pattern=r"^[0-9]+:[0-9]+$")
    pixel_aspect_ratio: Literal["1:1"] = "1:1"
    color_space: Literal["rec709"] = "rec709"
    title_safe_percent: int = Field(ge=50, le=100)
    action_safe_percent: int = Field(ge=50, le=100)
    expected_duration_frames: int = Field(gt=0)
    expected_duration_ms: int = Field(gt=0, le=3_600_000)


class TimelineValidationSummary(ContractModel):
    gaps: int = Field(ge=0)
    overlaps: int = Field(ge=0)
    missing_assets: int = Field(ge=0)
    corrupt_assets: int = Field(ge=0)
    duplicate_assets: int = Field(ge=0)
    duration_mismatches: int = Field(ge=0)
    frame_inconsistencies: int = Field(ge=0)
    orphan_assets: int = Field(ge=0)
    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)


class MediaCompositionPlan(ContractModel):
    schema_version: str = MEDIA_COMPOSITION_SCHEMA_VERSION
    plan_version: str = MEDIA_COMPOSITION_SCHEMA_VERSION
    job_id: UUID
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeline_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_manifests: tuple[SourceManifestReference, ...] = Field(min_length=3, max_length=10)
    assets: tuple[CompositionAssetReference, ...] = Field(min_length=1, max_length=5_000)
    output: OutputVideoSpecification
    tracks: tuple[CompositionTrack, ...] = Field(min_length=5, max_length=5)
    transitions: tuple[CompositionTransition, ...] = Field(default=(), max_length=5_000)
    ducking: tuple[DuckingInstruction, ...] = Field(default=(), max_length=500)
    subtitle_cues: tuple[SubtitleCue, ...] = Field(default=(), max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="media_composition_plan.metadata")
        if not isinstance(result, dict):
            raise ValueError("media composition metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_plan(self) -> MediaCompositionPlan:
        if self.schema_version not in SUPPORTED_MEDIA_COMPOSITION_VERSIONS:
            raise ValueError("media composition schema is unsupported")
        if tuple(track.order for track in self.tracks) != tuple(range(5)):
            raise ValueError("composition tracks must use stable ordering")
        expected_kinds = tuple(CompositionTrackKind)
        if tuple(track.kind for track in self.tracks) != expected_kinds:
            raise ValueError("composition track kinds are not canonical")
        manifest_types = tuple(item.artifact_type.value for item in self.source_manifests)
        if manifest_types != tuple(sorted(manifest_types)) or len(manifest_types) != len(
            set(manifest_types)
        ):
            raise ValueError("source manifest references must be unique and sorted")
        asset_ids = tuple(asset.asset_id for asset in self.assets)
        if len(asset_ids) != len(set(asset_ids)) or asset_ids != tuple(sorted(asset_ids)):
            raise ValueError("composition assets must be unique and sorted")
        clip_asset_ids = {clip.asset_id for track in self.tracks for clip in track.clips}
        if not clip_asset_ids.issubset(set(asset_ids)):
            raise ValueError("timeline clip references an unknown asset")
        for track in self.tracks:
            expected_kind = CompositionAssetKind(track.kind.value)
            if any(clip.kind is not expected_kind for clip in track.clips):
                raise ValueError("track contains a clip of another kind")
        video = self.tracks[0].clips
        if (
            not video
            or video[0].timeline_start_frame != 0
            or video[-1].timeline_end_frame != self.output.expected_duration_frames
        ):
            raise ValueError("video track must cover the complete timeline")
        for before, after in zip(video, video[1:], strict=False):
            if before.timeline_end_frame != after.timeline_start_frame:
                raise ValueError("video track contains a gap or overlap")
        transition_indexes = tuple(item.sequence_index for item in self.transitions)
        if transition_indexes != tuple(range(len(self.transitions))):
            raise ValueError("transitions must use consecutive ordering")
        return self


class CompositionAssetValidation(ContractModel):
    asset_id: str
    availability: CompositionAssetAvailability
    relative_path: str
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actual_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    issue_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return validate_relative_path(value)


class CompositionValidationIssue(ContractModel):
    code: str = Field(pattern=r"^[a-z0-9_]{1,100}$")
    severity: CompositionIssueSeverity
    asset_id: str | None = None
    track_id: str | None = None


class MediaCompositionManifest(ContractModel):
    schema_version: str = MEDIA_COMPOSITION_SCHEMA_VERSION
    plan_version: str = MEDIA_COMPOSITION_SCHEMA_VERSION
    job_id: UUID
    stage: Literal[ProductionStage.BUILDING_TIMELINE] = ProductionStage.BUILDING_TIMELINE
    attempt_number: int = Field(ge=1)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeline_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_relative_path: str
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_size_bytes: int = Field(gt=0, le=16_000_000)
    asset_inventory: tuple[CompositionAssetValidation, ...] = Field(
        min_length=1,
        max_length=5_000,
    )
    validation_summary: TimelineValidationSummary
    issues: tuple[CompositionValidationIssue, ...] = Field(default=(), max_length=5_000)
    status: CompositionManifestStatus
    generated_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("plan_relative_path")
    @classmethod
    def safe_plan_path(cls, value: str) -> str:
        return validate_relative_path(value)

    @field_validator("generated_at", "updated_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("composition manifest timestamps must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="media_composition_manifest.metadata")
        if not isinstance(result, dict):
            raise ValueError("media composition manifest metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_manifest(self) -> MediaCompositionManifest:
        if self.schema_version not in SUPPORTED_MEDIA_COMPOSITION_VERSIONS:
            raise ValueError("media composition manifest schema is unsupported")
        if self.updated_at < self.generated_at:
            raise ValueError("manifest update precedes generation")
        ids = tuple(item.asset_id for item in self.asset_inventory)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("manifest asset inventory must be unique and sorted")
        unavailable = sum(
            item.availability is not CompositionAssetAvailability.AVAILABLE
            for item in self.asset_inventory
        )
        if self.status is CompositionManifestStatus.COMPLETE and unavailable:
            raise ValueError("complete manifest requires all assets available")
        if self.status is CompositionManifestStatus.INVALIDATED and not unavailable:
            raise ValueError("invalidated manifest requires an unavailable asset")
        return self


class MediaCompositionReconciliationResult(ContractModel):
    job_id: UUID
    attempt_number: int = Field(ge=1)
    plan_present: bool
    manifest_present: bool
    plan_valid: bool
    manifest_valid: bool
    source_matches: bool
    expected_asset_count: int = Field(ge=0)
    available_asset_count: int = Field(ge=0)
    missing_asset_ids: tuple[str, ...] = ()
    corrupt_asset_ids: tuple[str, ...] = ()
    orphan_asset_ids: tuple[str, ...] = ()
    recovery_safe: bool
    manual_intervention_required: bool
    stage_complete: bool
    issues: tuple[CompositionValidationIssue, ...] = ()
