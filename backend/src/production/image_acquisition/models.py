"""Provider-neutral image acquisition and durable manifest contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.visual_asset_planning.models import (
    GenerationMode,
    VisualAssetRole,
)

SUPPORTED_IMAGE_ACQUISITION_MANIFEST_VERSIONS = frozenset({"1.0.0"})


class ImageAcquisitionEntryStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    STORED = "stored"
    FAILED_PERMANENT = "failed_permanent"
    FAILED_TRANSIENT = "failed_transient"
    UNCERTAIN = "uncertain"


class ImageAcquisitionManifestStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class OpenRouterImageRequestStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTING = "submitting"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class ProductionImageAcquisitionEntry(ContractModel):
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    scene_number: int = Field(ge=1, le=50)
    source_scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_number: int = Field(ge=1, le=100)
    source_shot_id: str = Field(
        pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$"
    )
    role: VisualAssetRole
    generation_mode: GenerationMode
    status: ImageAcquisitionEntryStatus
    binary_asset_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
    )
    binary_artifact_id: UUID | None = None
    storage_path: str | None = None
    mime_type: str | None = Field(default=None, max_length=100)
    extension: str | None = Field(default=None, pattern=r"^[a-z0-9]{2,5}$")
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, gt=0, le=250_000_000)
    width: int | None = Field(default=None, gt=0, le=16_384)
    height: int | None = Field(default=None, gt=0, le=16_384)
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    requested_model: str | None = Field(default=None, min_length=1, max_length=300)
    reported_model: str | None = Field(default=None, min_length=1, max_length=300)
    provider_request_id: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    http_status: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float | None = Field(default=None, ge=0)
    finish_reason: str | None = Field(default=None, max_length=100)
    attempt_number: int = Field(ge=1)
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]{1,100}$",
    )
    request_status: OpenRouterImageRequestStatus | None = None
    request_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    fresh_submission_permitted: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("storage_path")
    @classmethod
    def validate_storage_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("image acquisition storage path must be POSIX")
        return normalized

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="image_acquisition_entry.metadata")
        if not isinstance(result, dict):
            raise ValueError("image acquisition entry metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_state_payload(self) -> ProductionImageAcquisitionEntry:
        stored_fields = (
            self.binary_asset_id,
            self.binary_artifact_id,
            self.storage_path,
            self.mime_type,
            self.extension,
            self.sha256,
            self.size_bytes,
            self.width,
            self.height,
            self.provider,
        )
        if self.status is ImageAcquisitionEntryStatus.STORED:
            if any(value is None for value in stored_fields):
                raise ValueError("stored image entry requires complete binary metadata")
            if self.error_code is not None:
                raise ValueError("stored image entry cannot contain an error")
        elif any(value is not None for value in stored_fields):
            raise ValueError("non-stored entry cannot claim durable binary metadata")
        if self.status in {
            ImageAcquisitionEntryStatus.FAILED_PERMANENT,
            ImageAcquisitionEntryStatus.FAILED_TRANSIENT,
            ImageAcquisitionEntryStatus.UNCERTAIN,
        } and self.error_code is None:
            raise ValueError("failed or uncertain entry requires an error code")
        if self.source_scene_id != f"scene-{self.scene_number:03d}":
            raise ValueError("entry scene ID must match scene number")
        expected_shot = (
            f"scene-{self.scene_number:03d}-shot-{self.shot_number:03d}"
        )
        if self.source_shot_id != expected_shot:
            raise ValueError("entry shot ID must match scene and shot numbers")
        if self.request_status is not None:
            if self.request_fingerprint is None or self.fresh_submission_permitted is None:
                raise ValueError("remote image request checkpoint is incomplete")
            if (
                self.request_status is OpenRouterImageRequestStatus.PREPARED
            ) != self.fresh_submission_permitted:
                raise ValueError("remote image fresh-submission policy is inconsistent")
        return self


class ProductionImageAcquisitionSummary(ContractModel):
    total: int = Field(ge=0)
    stored: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    uncertain: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> ProductionImageAcquisitionSummary:
        if self.stored + self.pending + self.failed + self.uncertain != self.total:
            raise ValueError("image acquisition summary counts must equal total")
        return self


class ProductionImageAcquisitionManifest(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_visual_asset_plan_schema_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$"
    )
    source_visual_asset_plan_artifact_id: UUID
    source_visual_asset_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=100)
    requested_model: str | None = Field(default=None, max_length=300)
    reported_models: tuple[str, ...] = Field(default=(), max_length=100)
    status: ImageAcquisitionManifestStatus
    entries: tuple[ProductionImageAcquisitionEntry, ...] = Field(
        min_length=1,
        max_length=500,
    )
    summary: ProductionImageAcquisitionSummary
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="image_acquisition_manifest.metadata")
        if not isinstance(result, dict):
            raise ValueError("image acquisition manifest metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_manifest(self) -> ProductionImageAcquisitionManifest:
        if self.schema_version not in SUPPORTED_IMAGE_ACQUISITION_MANIFEST_VERSIONS:
            raise ValueError("image acquisition manifest version is unsupported")
        identifiers = tuple(entry.visual_asset_id for entry in self.entries)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("image acquisition entries must be unique")
        if identifiers != tuple(sorted(identifiers)):
            raise ValueError("image acquisition entries must be deterministic")
        expected = summarize_entries(self.entries)
        if self.summary != expected:
            raise ValueError("image acquisition summary does not match entries")
        if self.status is ImageAcquisitionManifestStatus.COMPLETED and any(
            entry.status is not ImageAcquisitionEntryStatus.STORED
            for entry in self.entries
        ):
            raise ValueError("completed manifest requires every image to be stored")
        if self.status is ImageAcquisitionManifestStatus.UNCERTAIN and not any(
            entry.status is ImageAcquisitionEntryStatus.UNCERTAIN
            for entry in self.entries
        ):
            raise ValueError("uncertain manifest requires an uncertain entry")
        return self


def summarize_entries(
    entries: tuple[ProductionImageAcquisitionEntry, ...],
) -> ProductionImageAcquisitionSummary:
    stored = sum(
        entry.status is ImageAcquisitionEntryStatus.STORED for entry in entries
    )
    pending = sum(
        entry.status
        in {
            ImageAcquisitionEntryStatus.PENDING,
            ImageAcquisitionEntryStatus.GENERATING,
        }
        for entry in entries
    )
    failed = sum(
        entry.status
        in {
            ImageAcquisitionEntryStatus.FAILED_PERMANENT,
            ImageAcquisitionEntryStatus.FAILED_TRANSIENT,
        }
        for entry in entries
    )
    uncertain = sum(
        entry.status is ImageAcquisitionEntryStatus.UNCERTAIN for entry in entries
    )
    return ProductionImageAcquisitionSummary(
        total=len(entries),
        stored=stored,
        pending=pending,
        failed=failed,
        uncertain=uncertain,
    )


def replace_manifest_entry(
    manifest: ProductionImageAcquisitionManifest,
    replacement: ProductionImageAcquisitionEntry,
    *,
    status: ImageAcquisitionManifestStatus | None = None,
) -> ProductionImageAcquisitionManifest:
    entries = tuple(
        replacement if item.visual_asset_id == replacement.visual_asset_id else item
        for item in manifest.entries
    )
    if entries == manifest.entries and all(
        item.visual_asset_id != replacement.visual_asset_id
        for item in manifest.entries
    ):
        raise ImageAcquisitionTransitionError("replacement entry is not in manifest")
    reported_models = tuple(
        sorted(
            {
                entry.reported_model
                for entry in entries
                if entry.reported_model is not None
            }
        )
    )
    payload = manifest.model_dump(mode="python")
    payload.update(
        {
            "entries": entries,
            "summary": summarize_entries(entries),
            "status": status or manifest.status,
            "reported_models": reported_models,
        }
    )
    return ProductionImageAcquisitionManifest.model_validate(payload)


class ImageAcquisitionTransitionError(ValueError):
    pass


def validate_manifest_transition(
    previous: ProductionImageAcquisitionManifest,
    current: ProductionImageAcquisitionManifest,
) -> None:
    if (
        previous.source_visual_asset_plan_artifact_id
        != current.source_visual_asset_plan_artifact_id
        or previous.source_visual_asset_plan_sha256
        != current.source_visual_asset_plan_sha256
        or tuple(entry.visual_asset_id for entry in previous.entries)
        != tuple(entry.visual_asset_id for entry in current.entries)
    ):
        raise ImageAcquisitionTransitionError(
            "manifest checkpoint cannot change source or entry identity"
        )
    allowed = {
        ImageAcquisitionEntryStatus.PENDING: {
            ImageAcquisitionEntryStatus.PENDING,
            ImageAcquisitionEntryStatus.GENERATING,
            ImageAcquisitionEntryStatus.STORED,
        },
        ImageAcquisitionEntryStatus.GENERATING: {
            ImageAcquisitionEntryStatus.GENERATING,
            ImageAcquisitionEntryStatus.STORED,
            ImageAcquisitionEntryStatus.FAILED_PERMANENT,
            ImageAcquisitionEntryStatus.FAILED_TRANSIENT,
            ImageAcquisitionEntryStatus.UNCERTAIN,
        },
        ImageAcquisitionEntryStatus.STORED: {
            ImageAcquisitionEntryStatus.STORED,
        },
        ImageAcquisitionEntryStatus.FAILED_TRANSIENT: {
            ImageAcquisitionEntryStatus.FAILED_TRANSIENT,
        },
        ImageAcquisitionEntryStatus.FAILED_PERMANENT: {
            ImageAcquisitionEntryStatus.FAILED_PERMANENT,
        },
        ImageAcquisitionEntryStatus.UNCERTAIN: {
            ImageAcquisitionEntryStatus.UNCERTAIN,
        },
    }
    for old, new in zip(previous.entries, current.entries, strict=True):
        if new.status not in allowed[old.status]:
            raise ImageAcquisitionTransitionError(
                f"invalid image checkpoint transition {old.status} -> {new.status}"
            )
