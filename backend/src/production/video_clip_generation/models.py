"""Strict contracts for durable video clip generation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.visual_asset_planning.models import VisualAssetRole

SUPPORTED_VIDEO_CLIP_MANIFEST_VERSIONS = frozenset({"1.0.0"})
SUPPORTED_VIDEO_CLIP_ASSET_VERSIONS = frozenset({"1.0.0"})


class VideoClipEntryStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    STORED = "stored"
    FAILED_PERMANENT = "failed_permanent"
    FAILED_TRANSIENT = "failed_transient"
    UNCERTAIN = "uncertain"


class VideoClipManifestStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class VideoClipGenerationMode(StrEnum):
    STILL_IMAGE_TO_VIDEO = "still_image_to_video"


class VideoClipRemoteStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VideoClipMetadata(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_image_manifest_artifact_id: UUID
    source_image_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_image_artifact_id: UUID
    source_image_binary_asset_id: str
    source_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_visual_asset_id: str
    source_scene_id: str
    source_shot_id: str
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str
    requested_model: str
    reported_model: str
    deterministic: bool
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def safe_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="video_clip_metadata.attributes")
        if not isinstance(result, dict):
            raise ValueError("video clip metadata attributes must be an object")
        return result


class ProductionVideoClipAsset(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    asset_id: str = Field(pattern=r"^video-asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    job_id: UUID
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    role: VisualAssetRole
    mime_type: str = "video/mp4"
    extension: str = "mp4"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=250_000_000)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    duration_seconds: float = Field(gt=0, le=10)
    frame_rate: float = Field(gt=0, le=120)
    frame_count: int = Field(gt=0)
    video_codec: str = "h264"
    audio_codec: str | None = None
    has_audio: bool = False
    created_at: datetime
    storage_path: str
    metadata: VideoClipMetadata

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("video clip creation time must be timezone-aware")
        return value

    @field_validator("storage_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("video clip path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def validate_asset(self) -> ProductionVideoClipAsset:
        if self.schema_version not in SUPPORTED_VIDEO_CLIP_ASSET_VERSIONS:
            raise ValueError("video clip asset schema version is unsupported")
        expected = f"production/{self.job_id}/assets/video-clips/{self.asset_id}.mp4"
        if self.storage_path != expected:
            raise ValueError("video clip path is not contractual")
        if self.mime_type != "video/mp4" or self.extension != "mp4":
            raise ValueError("video clip format must be MP4")
        if self.has_audio or self.audio_codec is not None:
            raise ValueError("video clips cannot contain audio")
        if self.shot_id.rsplit("-shot-", 1)[0] != self.scene_id:
            raise ValueError("video clip shot must belong to its scene")
        return self


class VideoClipWriteRequest(ContractModel):
    job_id: UUID
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    role: VisualAssetRole
    expected_width: int = Field(gt=0, le=16_384)
    expected_height: int = Field(gt=0, le=16_384)
    expected_duration_seconds: float = Field(gt=0, le=10)
    expected_frame_rate: int = Field(gt=0, le=120)
    metadata: VideoClipMetadata

    @property
    def asset_id(self) -> str:
        return f"video-{self.visual_asset_id}"


class ReadProductionVideoClipAsset(ContractModel):
    asset: ProductionVideoClipAsset
    content: bytes = Field(repr=False, exclude=True)


class ProductionVideoClipEntry(ContractModel):
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    source_image_artifact_id: UUID
    source_image_binary_asset_id: str
    source_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_scene_id: str
    source_shot_id: str
    scene_number: int = Field(ge=1)
    shot_number: int = Field(ge=1)
    role: VisualAssetRole
    generation_mode: VideoClipGenerationMode = VideoClipGenerationMode.STILL_IMAGE_TO_VIDEO
    status: VideoClipEntryStatus
    video_binary_asset_id: str | None = None
    video_artifact_id: UUID | None = None
    storage_path: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    frame_rate: float | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, gt=0)
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool | None = None
    provider: str | None = None
    requested_model: str | None = None
    reported_model: str | None = None
    provider_request_id: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    attempt_number: int = Field(ge=1)
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    remote_provider: str | None = Field(default=None, max_length=100)
    remote_job_id: str | None = Field(default=None, max_length=200)
    remote_generation_id: str | None = Field(default=None, max_length=200)
    remote_status: VideoClipRemoteStatus | None = None
    remote_submitted_at: datetime | None = None
    remote_last_polled_at: datetime | None = None
    remote_poll_attempts: int | None = Field(default=None, ge=0)
    remote_terminal_at: datetime | None = None
    remote_content_available: bool | None = None
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    reported_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    pricing_snapshot_at: datetime | None = None
    pricing_sku: str | None = Field(default=None, max_length=100)
    prompt_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_publication_id: str | None = Field(default=None, max_length=200)
    source_publication_expires_at: datetime | None = None
    publication_provider: str | None = Field(default=None, max_length=100)
    provider_request_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    capability_snapshot_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    remote_url_metadata: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "remote_submitted_at",
        "remote_last_polled_at",
        "remote_terminal_at",
        "pricing_snapshot_at",
        "source_publication_expires_at",
    )
    @classmethod
    def aware_remote_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("video clip remote timestamps must be timezone-aware")
        return value

    @field_validator("metadata", "remote_url_metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="video_clip_entry.metadata")
        if not isinstance(result, dict):
            raise ValueError("video clip entry metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_entry(self) -> ProductionVideoClipEntry:
        expected_scene = f"scene-{self.scene_number:03d}"
        expected_shot = f"{expected_scene}-shot-{self.shot_number:03d}"
        if self.source_scene_id != expected_scene or self.source_shot_id != expected_shot:
            raise ValueError("video clip scene/shot mapping is inconsistent")
        stored = (
            self.video_binary_asset_id,
            self.video_artifact_id,
            self.storage_path,
            self.mime_type,
            self.extension,
            self.sha256,
            self.size_bytes,
            self.width,
            self.height,
            self.duration_seconds,
            self.frame_rate,
            self.frame_count,
            self.video_codec,
            self.has_audio,
            self.provider,
        )
        if self.status is VideoClipEntryStatus.STORED:
            if any(item is None for item in stored):
                raise ValueError("stored video clip entry requires complete metadata")
            if self.mime_type != "video/mp4" or self.extension != "mp4":
                raise ValueError("stored video clip must be MP4")
            if self.has_audio or self.audio_codec is not None:
                raise ValueError("stored video clip cannot contain audio")
            if self.error_code is not None:
                raise ValueError("stored video clip cannot contain an error")
        elif any(item is not None for item in stored):
            raise ValueError("non-stored entry cannot claim durable video metadata")
        if (
            self.status
            in {
                VideoClipEntryStatus.FAILED_PERMANENT,
                VideoClipEntryStatus.FAILED_TRANSIENT,
                VideoClipEntryStatus.UNCERTAIN,
            }
            and self.error_code is None
        ):
            raise ValueError("failed or uncertain entry requires an error code")
        remote_fields = (
            self.remote_job_id,
            self.remote_status,
            self.remote_submitted_at,
            self.remote_poll_attempts,
            self.estimated_cost_usd,
            self.prompt_sha256,
            self.source_publication_id,
            self.publication_provider,
            self.provider_request_fingerprint,
            self.capability_snapshot_hash,
        )
        if self.remote_provider == "openrouter" and self.status is VideoClipEntryStatus.STORED:
            if any(item is None for item in remote_fields):
                raise ValueError("stored OpenRouter clip requires durable remote metadata")
            if (
                self.remote_status is not VideoClipRemoteStatus.COMPLETED
                or self.remote_content_available is not True
            ):
                raise ValueError("stored OpenRouter clip requires completed remote state")
        return self


class ProductionVideoClipSummary(ContractModel):
    total: int = Field(ge=0)
    stored: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    uncertain: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> ProductionVideoClipSummary:
        if self.stored + self.pending + self.failed + self.uncertain != self.total:
            raise ValueError("video clip summary counts must equal total")
        return self


class ProductionVideoClipManifest(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_image_manifest_schema_version: str
    source_image_manifest_artifact_id: UUID
    source_image_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str
    requested_model: str
    reported_models: tuple[str, ...] = ()
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: tuple[ProductionVideoClipEntry, ...] = Field(min_length=1, max_length=500)
    summary: ProductionVideoClipSummary
    status: VideoClipManifestStatus
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="video_clip_manifest.metadata")
        if not isinstance(result, dict):
            raise ValueError("video clip manifest metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_manifest(self) -> ProductionVideoClipManifest:
        if self.schema_version not in SUPPORTED_VIDEO_CLIP_MANIFEST_VERSIONS:
            raise ValueError("video clip manifest version is unsupported")
        identifiers = tuple(entry.visual_asset_id for entry in self.entries)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("video clip entries must be unique")
        if identifiers != tuple(sorted(identifiers)):
            raise ValueError("video clip entries must be deterministic")
        if self.summary != summarize_entries(self.entries):
            raise ValueError("video clip summary does not match entries")
        if self.status is VideoClipManifestStatus.COMPLETED and any(
            entry.status is not VideoClipEntryStatus.STORED for entry in self.entries
        ):
            raise ValueError("completed video clip manifest requires all entries stored")
        if self.status is VideoClipManifestStatus.UNCERTAIN and not any(
            entry.status is VideoClipEntryStatus.UNCERTAIN for entry in self.entries
        ):
            raise ValueError("uncertain manifest requires an uncertain entry")
        return self


def summarize_entries(
    entries: tuple[ProductionVideoClipEntry, ...],
) -> ProductionVideoClipSummary:
    return ProductionVideoClipSummary(
        total=len(entries),
        stored=sum(entry.status is VideoClipEntryStatus.STORED for entry in entries),
        pending=sum(
            entry.status in {VideoClipEntryStatus.PENDING, VideoClipEntryStatus.GENERATING}
            for entry in entries
        ),
        failed=sum(
            entry.status
            in {
                VideoClipEntryStatus.FAILED_PERMANENT,
                VideoClipEntryStatus.FAILED_TRANSIENT,
            }
            for entry in entries
        ),
        uncertain=sum(entry.status is VideoClipEntryStatus.UNCERTAIN for entry in entries),
    )


def replace_manifest_entry(
    manifest: ProductionVideoClipManifest,
    entry: ProductionVideoClipEntry,
    *,
    status: VideoClipManifestStatus | None = None,
) -> ProductionVideoClipManifest:
    entries = tuple(
        entry if current.visual_asset_id == entry.visual_asset_id else current
        for current in manifest.entries
    )
    return manifest.model_copy(
        update={
            "entries": entries,
            "summary": summarize_entries(entries),
            "status": status or manifest.status,
            "reported_models": tuple(
                sorted(
                    {
                        current.reported_model
                        for current in entries
                        if current.reported_model is not None
                    }
                )
            ),
        }
    )


_ALLOWED_TRANSITIONS = {
    VideoClipEntryStatus.PENDING: {
        VideoClipEntryStatus.PENDING,
        VideoClipEntryStatus.GENERATING,
        VideoClipEntryStatus.FAILED_PERMANENT,
        VideoClipEntryStatus.FAILED_TRANSIENT,
    },
    VideoClipEntryStatus.GENERATING: {
        VideoClipEntryStatus.GENERATING,
        VideoClipEntryStatus.STORED,
        VideoClipEntryStatus.FAILED_PERMANENT,
        VideoClipEntryStatus.FAILED_TRANSIENT,
        VideoClipEntryStatus.UNCERTAIN,
    },
    VideoClipEntryStatus.STORED: {VideoClipEntryStatus.STORED},
    VideoClipEntryStatus.FAILED_PERMANENT: {VideoClipEntryStatus.FAILED_PERMANENT},
    VideoClipEntryStatus.FAILED_TRANSIENT: {VideoClipEntryStatus.FAILED_TRANSIENT},
    VideoClipEntryStatus.UNCERTAIN: {VideoClipEntryStatus.UNCERTAIN},
}


def validate_manifest_transition(
    previous: ProductionVideoClipManifest,
    current: ProductionVideoClipManifest,
) -> None:
    if (
        previous.schema_version != current.schema_version
        or previous.source_image_manifest_artifact_id != current.source_image_manifest_artifact_id
        or previous.source_image_manifest_sha256 != current.source_image_manifest_sha256
        or previous.configuration_fingerprint != current.configuration_fingerprint
        or tuple(entry.visual_asset_id for entry in previous.entries)
        != tuple(entry.visual_asset_id for entry in current.entries)
    ):
        raise ValueError("video clip manifest immutable fields changed")
    for before, after in zip(previous.entries, current.entries, strict=True):
        if after.status not in _ALLOWED_TRANSITIONS[before.status]:
            raise ValueError(
                f"invalid video clip transition: {before.status.value} -> {after.status.value}"
            )
