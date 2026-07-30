"""Strict contracts for renderer-neutral local preparation."""

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
from backend.src.production.media_composition.domain.models import (
    CompositionAssetKind,
)

LOCAL_RENDER_SCHEMA_VERSION = "1.0.0"
RENDERER_CONTRACT_VERSION = "1.0.0"
SUPPORTED_LOCAL_RENDER_VERSIONS = frozenset({LOCAL_RENDER_SCHEMA_VERSION})


class RendererKind(StrEnum):
    DRY_RUN = "dry_run"
    FFMPEG = "ffmpeg"
    DAVINCI_RESOLVE = "davinci_resolve"


class RendererActivationState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class RendererReadiness(StrEnum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    EXECUTABLE_MISSING = "executable_missing"
    UNSUPPORTED_PLAN = "unsupported_plan"
    DISABLED_BY_POLICY = "disabled_by_policy"


class RenderManifestStatus(StrEnum):
    PREPARED = "prepared"
    VALIDATING = "validating"
    VALIDATED = "validated"
    INVALID = "invalid"
    FAILED = "failed"


class OverwritePolicy(StrEnum):
    FAIL_IF_EXISTS = "fail_if_exists"


class RendererCapabilities(ContractModel):
    renderer_kind: RendererKind
    renderer_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    local_only: Literal[True] = True
    produces_media: bool
    supported_container_formats: tuple[str, ...] = ()
    supported_video_codecs: tuple[str, ...] = ()
    supported_audio_codecs: tuple[str, ...] = ()
    supports_video_tracks: bool
    supports_narration: bool
    supports_music: bool
    supports_sound_effects: bool
    supports_subtitles: bool
    supports_transitions: bool
    supports_volume_envelopes: bool
    supports_ducking: bool
    supports_fades: bool
    supports_vertical_video: bool
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    max_frame_rate: int | None = Field(default=None, gt=0)
    deterministic_preparation: bool

    @model_validator(mode="after")
    def conservative_non_rendering_capabilities(self) -> RendererCapabilities:
        if not self.produces_media and any(
            (
                self.supported_container_formats,
                self.supported_video_codecs,
                self.supported_audio_codecs,
            )
        ):
            raise ValueError("non-rendering capability cannot claim output codec support")
        return self


class RendererDescription(ContractModel):
    renderer_kind: RendererKind
    activation_state: RendererActivationState
    readiness: RendererReadiness
    capabilities: RendererCapabilities

    @model_validator(mode="after")
    def consistent_identity(self) -> RendererDescription:
        if self.renderer_kind is not self.capabilities.renderer_kind:
            raise ValueError("renderer capability identity differs")
        if self.activation_state is RendererActivationState.ACTIVE:
            if self.renderer_kind is not RendererKind.DRY_RUN:
                raise ValueError("only dry_run can be active in this contract")
            if self.readiness is not RendererReadiness.READY:
                raise ValueError("active renderer must be ready")
        if self.renderer_kind is not RendererKind.DRY_RUN and (
            self.activation_state is not RendererActivationState.DISABLED
            or self.readiness is not RendererReadiness.NOT_CONFIGURED
        ):
            raise ValueError("future renderers must remain disabled and not configured")
        return self


class RequestedRenderOutput(ContractModel):
    container_format: Literal["mp4"] = "mp4"
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    filename: str = Field(pattern=r"^orion-[0-9a-f-]{36}-[a-f0-9]{12}\.mp4$")
    relative_path: str
    overwrite_policy: Literal[OverwritePolicy.FAIL_IF_EXISTS] = OverwritePolicy.FAIL_IF_EXISTS
    pixel_format: Literal["yuv420p"] = "yuv420p"
    include_subtitles: bool
    expected_mime_type: Literal["video/mp4"] = "video/mp4"

    @field_validator("relative_path")
    @classmethod
    def safe_output_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("render output path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def matching_filename(self) -> RequestedRenderOutput:
        if self.relative_path.split("/")[-1] != self.filename:
            raise ValueError("render output filename differs from its path")
        return self


class RenderTrackSummary(ContractModel):
    track_count: int = Field(ge=1, le=20)
    enabled_track_count: int = Field(ge=1, le=20)
    clip_count: int = Field(ge=1, le=20_000)
    video_clip_count: int = Field(ge=1, le=5_000)
    narration_clip_count: int = Field(ge=1, le=5_000)
    music_clip_count: int = Field(ge=0, le=5_000)
    sound_effect_clip_count: int = Field(ge=0, le=5_000)
    subtitle_clip_count: int = Field(ge=0, le=10_000)
    transition_count: int = Field(ge=0, le=5_000)
    subtitle_cue_count: int = Field(ge=0, le=10_000)
    volume_envelope_count: int = Field(ge=0, le=20_000)
    ducking_instruction_count: int = Field(ge=0, le=500)
    fade_clip_count: int = Field(ge=0, le=20_000)
    has_video: bool
    has_narration: bool
    has_music: bool
    has_sound_effects: bool
    has_subtitles: bool
    has_transitions: bool
    has_volume_envelopes: bool
    has_ducking: bool
    has_fades: bool

    @model_validator(mode="after")
    def consistent_flags(self) -> RenderTrackSummary:
        pairs = (
            (self.has_video, self.video_clip_count),
            (self.has_narration, self.narration_clip_count),
            (self.has_music, self.music_clip_count),
            (self.has_sound_effects, self.sound_effect_clip_count),
            (self.has_subtitles, self.subtitle_cue_count + self.subtitle_clip_count),
            (self.has_transitions, self.transition_count),
            (self.has_volume_envelopes, self.volume_envelope_count),
            (self.has_ducking, self.ducking_instruction_count),
            (self.has_fades, self.fade_clip_count),
        )
        if any(flag != bool(count) for flag, count in pairs):
            raise ValueError("render track summary flags differ from counts")
        if self.enabled_track_count > self.track_count:
            raise ValueError("enabled track count exceeds total tracks")
        return self


class RenderAssetReference(ContractModel):
    asset_id: str = Field(min_length=1, max_length=200)
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_kind: CompositionAssetKind
    duration_ms: int | None = Field(default=None, gt=0, le=3_600_000)

    @field_validator("relative_path")
    @classmethod
    def safe_asset_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("render asset path must use POSIX separators")
        return normalized


class LocalRenderRequest(ContractModel):
    schema_version: str = LOCAL_RENDER_SCHEMA_VERSION
    request_id: UUID
    job_id: UUID
    renderer_kind: Literal[RendererKind.DRY_RUN] = RendererKind.DRY_RUN
    source_plan_artifact_id: UUID
    source_plan_relative_path: str
    source_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeline_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_duration_ms: int = Field(gt=0, le=3_600_000)
    expected_duration_frames: int = Field(gt=0)
    output_width: int = Field(gt=0, le=16_384)
    output_height: int = Field(gt=0, le=16_384)
    frame_rate_numerator: int = Field(gt=0, le=120)
    frame_rate_denominator: int = Field(gt=0, le=1_000)
    aspect_ratio: str = Field(pattern=r"^[0-9]+:[0-9]+$")
    color_space: Literal["rec709"] = "rec709"
    track_summary: RenderTrackSummary
    asset_count: int = Field(gt=0, le=5_000)
    asset_fingerprints: tuple[RenderAssetReference, ...] = Field(
        min_length=1,
        max_length=5_000,
    )
    requested_output: RequestedRenderOutput
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    dry_run: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_plan_relative_path")
    @classmethod
    def safe_plan_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("source plan path must use POSIX separators")
        return normalized

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="local_render_request.metadata")
        if not isinstance(result, dict):
            raise ValueError("render request metadata must be an object")
        return result

    @model_validator(mode="after")
    def consistent_request(self) -> LocalRenderRequest:
        ids = tuple(item.asset_id for item in self.asset_fingerprints)
        if len(ids) != len(set(ids)) or ids != tuple(sorted(ids)):
            raise ValueError("render assets must be unique and ordered")
        if self.asset_count != len(self.asset_fingerprints):
            raise ValueError("render asset count differs")
        expected_prefix = f"production/{self.job_id}/output/"
        if not self.requested_output.relative_path.startswith(expected_prefix):
            raise ValueError("render output path belongs to another job")
        return self


class DryRunRenderResult(ContractModel):
    renderer_kind: Literal[RendererKind.DRY_RUN] = RendererKind.DRY_RUN
    renderer_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted: bool
    media_produced: Literal[False] = False
    output_created: Literal[False] = False
    validated_asset_count: int = Field(ge=0, le=5_000)
    validated_track_count: int = Field(ge=0, le=20)
    validation_codes: tuple[str, ...] = Field(max_length=100)
    deterministic: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("validation_codes")
    @classmethod
    def stable_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(value)) or len(value) != len(set(value)):
            raise ValueError("dry-run validation codes must be unique and sorted")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="dry_run_result.metadata")
        if not isinstance(result, dict):
            raise ValueError("dry-run result metadata must be an object")
        return result


class RenderExecutionManifest(ContractModel):
    schema_version: str = LOCAL_RENDER_SCHEMA_VERSION
    job_id: UUID
    stage: Literal[ProductionStage.RENDERING_LONG_FORM] = ProductionStage.RENDERING_LONG_FORM
    attempt_number: int = Field(ge=1)
    renderer_kind: Literal[RendererKind.DRY_RUN] = RendererKind.DRY_RUN
    renderer_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    source_plan_artifact_id: UUID
    source_plan_relative_path: str
    source_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeline_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_output: RequestedRenderOutput
    capabilities_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: RenderManifestStatus
    dry_run_result: DryRunRenderResult | None = None
    output_artifact_id: Literal[None] = None
    output_relative_path: str
    output_sha256: Literal[None] = None
    output_size_bytes: Literal[None] = None
    media_produced: Literal[False] = False
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_plan_relative_path", "output_relative_path")
    @classmethod
    def safe_paths(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("render manifest paths must use POSIX separators")
        return normalized

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("render manifest timestamps must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="render_execution_manifest.metadata")
        if not isinstance(result, dict):
            raise ValueError("render manifest metadata must be an object")
        return result

    @model_validator(mode="after")
    def phase_contract(self) -> RenderExecutionManifest:
        if self.updated_at < self.created_at:
            raise ValueError("render manifest update precedes creation")
        if self.output_relative_path != self.requested_output.relative_path:
            raise ValueError("render manifest output identity differs")
        if self.status is RenderManifestStatus.VALIDATED:
            if self.dry_run_result is None or not self.dry_run_result.accepted:
                raise ValueError("validated manifest requires an accepted dry-run result")
        elif self.dry_run_result is not None:
            raise ValueError("only validated manifests may contain a dry-run result")
        return self


class RenderReconciliationResult(ContractModel):
    job_id: UUID
    attempt_number: int = Field(ge=1)
    source_plan_present: bool
    source_manifest_present: bool
    request_present: bool
    execution_manifest_present: bool
    schemas_supported: bool
    source_identities_match: bool
    request_fingerprint_valid: bool
    renderer_kind: RendererKind
    renderer_readiness: RendererReadiness
    dry_run_result_present: bool
    dry_run_accepted: bool
    media_produced: bool
    unexpected_output_file: bool
    stale_source: bool
    corrupt_state: bool
    recovery_safe: bool
    manual_intervention_required: bool
    stage_complete: bool
    issues: tuple[str, ...] = ()

    @field_validator("issues")
    @classmethod
    def stable_issues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))
