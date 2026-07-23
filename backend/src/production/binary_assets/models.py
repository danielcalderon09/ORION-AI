"""Strict durable contracts for binary production assets."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path

SUPPORTED_BINARY_ASSET_SCHEMA_VERSIONS = frozenset({"1.0.0"})


class BinaryAssetRole(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    REFERENCE = "reference"
    BACKGROUND = "background"
    FOREGROUND = "foreground"
    OVERLAY = "overlay"
    TITLE_CARD = "title_card"


class ProductionBinaryAssetMetadata(ContractModel):
    """Safe provenance retained with a binary asset."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_visual_asset_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
    )
    source_visual_asset_plan_artifact_id: UUID | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=200)
    model_version: str | None = Field(default=None, min_length=1, max_length=300)
    deterministic: bool | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="binary_asset.metadata.attributes")
        if not isinstance(result, dict):
            raise ValueError("binary asset metadata attributes must be an object")
        return result


class ProductionBinaryAsset(ContractModel):
    """Immutable durable metadata for one stored image."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    job_id: UUID
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    asset_role: BinaryAssetRole
    mime_type: str = Field(min_length=1, max_length=100)
    extension: str = Field(pattern=r"^[a-z0-9]{2,5}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=250_000_000)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    created_at: datetime
    storage_path: str
    metadata: ProductionBinaryAssetMetadata = Field(
        default_factory=ProductionBinaryAssetMetadata
    )

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("binary asset creation time must be timezone-aware")
        return value

    @field_validator("storage_path")
    @classmethod
    def validate_storage_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("binary asset path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def validate_contractual_mapping(self) -> ProductionBinaryAsset:
        if self.schema_version not in SUPPORTED_BINARY_ASSET_SCHEMA_VERSIONS:
            raise ValueError("binary asset schema version is unsupported")
        scene_prefix = self.shot_id.rsplit("-shot-", maxsplit=1)[0]
        if scene_prefix != self.scene_id:
            raise ValueError("binary asset shot must belong to its scene")
        expected = (
            f"production/{self.job_id}/assets/images/"
            f"{self.asset_id}.{self.extension}"
        )
        if self.storage_path != expected:
            raise ValueError("binary asset path is not contractual")
        return self


class ProductionBinaryAssetReference(ContractModel):
    """Minimum immutable expectation required for a verified read."""

    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    job_id: UUID
    storage_path: str
    mime_type: str
    extension: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=250_000_000)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)

    @field_validator("storage_path")
    @classmethod
    def validate_storage_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("binary asset path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def validate_contractual_path(self) -> ProductionBinaryAssetReference:
        expected = (
            f"production/{self.job_id}/assets/images/"
            f"{self.asset_id}.{self.extension}"
        )
        if self.storage_path != expected:
            raise ValueError("binary asset reference path is not contractual")
        if PurePosixPath(self.storage_path).suffix != f".{self.extension}":
            raise ValueError("binary asset extension does not match its path")
        return self

    @classmethod
    def from_asset(
        cls, asset: ProductionBinaryAsset
    ) -> ProductionBinaryAssetReference:
        return cls(
            asset_id=asset.asset_id,
            job_id=asset.job_id,
            storage_path=asset.storage_path,
            mime_type=asset.mime_type,
            extension=asset.extension,
            sha256=asset.sha256,
            size_bytes=asset.size_bytes,
            width=asset.width,
            height=asset.height,
        )


class BinaryAssetWriteRequest(ContractModel):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    job_id: UUID
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    asset_role: BinaryAssetRole
    mime_type: str
    extension: str = Field(pattern=r"^[a-z0-9]{2,5}$")
    expected_width: int | None = Field(default=None, gt=0, le=16_384)
    expected_height: int | None = Field(default=None, gt=0, le=16_384)
    metadata: ProductionBinaryAssetMetadata = Field(
        default_factory=ProductionBinaryAssetMetadata
    )

    @model_validator(mode="after")
    def validate_scene_mapping(self) -> BinaryAssetWriteRequest:
        if self.shot_id.rsplit("-shot-", maxsplit=1)[0] != self.scene_id:
            raise ValueError("binary asset shot must belong to its scene")
        return self


class ReadProductionBinaryAsset(ContractModel):
    asset: ProductionBinaryAsset
    content: bytes


def binary_asset_relative_path(
    *,
    job_id: UUID,
    asset_id: str,
    extension: str,
) -> str:
    """Build the only supported binary image path."""

    return validate_relative_path(
        f"production/{job_id}/assets/images/{asset_id}.{extension}"
    )
