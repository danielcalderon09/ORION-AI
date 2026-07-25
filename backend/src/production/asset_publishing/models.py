"""Immutable contracts for temporary asset publication."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.asset_publishing.url_validation import (
    validate_public_https_url,
)
from backend.src.production.domain.base import ContractModel

PUBLISHED_ASSET_SCHEMA_VERSION = "1.0.0"
SUPPORTED_PUBLISHED_ASSET_SCHEMA_VERSIONS = frozenset(
    {PUBLISHED_ASSET_SCHEMA_VERSION}
)
SUPPORTED_PUBLISHED_CONTENT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "video/mp4"}
)


class PublishedAssetStatus(StrEnum):
    NOT_PUBLISHED = "not_published"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    EXPIRED = "expired"
    REMOVED = "removed"


class PublishedAssetManifestStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CLEANED = "cleaned"


class PublishedAssetSourceManifest(ContractModel):
    kind: Literal["image_acquisition", "video_clip_generation"]
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_id: UUID | None = None


class PublishedAssetMetadata(ContractModel):
    schema_version: str = PUBLISHED_ASSET_SCHEMA_VERSION
    publication_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    extension: str = Field(pattern=r"^[a-z0-9]{2,5}$")
    source_manifest_kind: Literal["image_acquisition", "video_clip_generation"]
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_id: UUID | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def safe_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="published_asset.metadata.attributes")
        if not isinstance(result, dict):
            raise ValueError("published asset attributes must be an object")
        _reject_embedded_urls(result)
        return result

    @model_validator(mode="after")
    def supported_version(self) -> PublishedAssetMetadata:
        if self.schema_version not in SUPPORTED_PUBLISHED_ASSET_SCHEMA_VERSIONS:
            raise ValueError("published asset metadata version is unsupported")
        return self


class PublishedAsset(ContractModel):
    schema_version: str = PUBLISHED_ASSET_SCHEMA_VERSION
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    binary_asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_at: datetime | None = None
    expires_at: datetime | None = None
    publisher: str = Field(min_length=1, max_length=100)
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0, le=250_000_000)
    public_url: str | None = Field(default=None, repr=False)
    url_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: PublishedAssetStatus
    attempt_count: int = Field(default=0, ge=0, le=100)
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]{1,100}$",
    )
    metadata: PublishedAssetMetadata

    @field_validator("published_at", "expires_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("publication timestamps must be timezone-aware")
        return value

    @field_validator("public_url")
    @classmethod
    def safe_public_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_public_https_url(value)

    @model_validator(mode="after")
    def validate_state(self) -> PublishedAsset:
        if self.schema_version not in SUPPORTED_PUBLISHED_ASSET_SCHEMA_VERSIONS:
            raise ValueError("published asset version is unsupported")
        if self.content_type not in SUPPORTED_PUBLISHED_CONTENT_TYPES:
            raise ValueError("published asset content type is unsupported")
        if self.status is PublishedAssetStatus.NOT_PUBLISHED:
            if any(
                value is not None
                for value in (
                    self.published_at,
                    self.expires_at,
                    self.public_url,
                    self.url_hash,
                    self.error_code,
                )
            ):
                raise ValueError("not-published asset contains publication state")
        elif self.status is PublishedAssetStatus.PUBLISHING:
            if (
                self.attempt_count < 1
                or self.published_at is None
                or self.expires_at is None
                or self.public_url is not None
            ):
                raise ValueError("publishing asset state is invalid")
        elif self.status is PublishedAssetStatus.PUBLISHED:
            if (
                self.attempt_count < 1
                or self.published_at is None
                or self.expires_at is None
                or self.public_url is None
                or self.url_hash is None
                or self.error_code is not None
                or self.expires_at <= self.published_at
            ):
                raise ValueError("published asset requires complete active metadata")
            if self.url_hash != _url_hash(self.public_url):
                raise ValueError("published asset URL hash differs")
        elif self.status is PublishedAssetStatus.FAILED:
            if (
                self.attempt_count < 1
                or self.error_code is None
                or self.public_url is not None
            ):
                raise ValueError("failed publication requires an attempt and error")
        elif self.status in {
            PublishedAssetStatus.EXPIRED,
            PublishedAssetStatus.REMOVED,
        }:
            if self.published_at is None or self.expires_at is None:
                raise ValueError("expired or removed publication requires provenance")
            if self.public_url is not None:
                raise ValueError("expired or removed publication cannot persist its URL")
        return self


class PublishedAssetSummary(ContractModel):
    total: int = Field(ge=0)
    not_published: int = Field(ge=0)
    publishing: int = Field(ge=0)
    published: int = Field(ge=0)
    failed: int = Field(ge=0)
    expired: int = Field(ge=0)
    removed: int = Field(ge=0)

    @model_validator(mode="after")
    def total_matches(self) -> PublishedAssetSummary:
        counted = (
            self.not_published
            + self.publishing
            + self.published
            + self.failed
            + self.expired
            + self.removed
        )
        if counted != self.total:
            raise ValueError("published asset summary counts do not equal total")
        return self


class PublishedAssetManifest(ContractModel):
    schema_version: str = PUBLISHED_ASSET_SCHEMA_VERSION
    job_id: UUID
    attempt_number: int = Field(ge=1)
    publisher: str = Field(min_length=1, max_length=100)
    source_manifests: tuple[PublishedAssetSourceManifest, ...] = Field(
        min_length=1,
        max_length=2,
    )
    entries: tuple[PublishedAsset, ...] = Field(min_length=1, max_length=1000)
    summary: PublishedAssetSummary
    status: PublishedAssetManifestStatus
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_manifest_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published manifest timestamps must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="published_asset_manifest.metadata")
        if not isinstance(result, dict):
            raise ValueError("published manifest metadata must be an object")
        _reject_embedded_urls(result)
        return result

    @model_validator(mode="after")
    def validate_manifest(self) -> PublishedAssetManifest:
        if self.schema_version not in SUPPORTED_PUBLISHED_ASSET_SCHEMA_VERSIONS:
            raise ValueError("published manifest version is unsupported")
        identifiers = tuple(entry.asset_id for entry in self.entries)
        if identifiers != tuple(sorted(identifiers)) or len(identifiers) != len(
            set(identifiers)
        ):
            raise ValueError("published manifest entries must be unique and sorted")
        source_kinds = tuple(item.kind for item in self.source_manifests)
        if source_kinds != tuple(sorted(source_kinds)) or len(source_kinds) != len(
            set(source_kinds)
        ):
            raise ValueError("source manifests must be unique and sorted")
        if self.summary != summarize_published_assets(self.entries):
            raise ValueError("published manifest summary differs from entries")
        if self.updated_at < self.created_at:
            raise ValueError("published manifest update time precedes creation")
        if self.status is PublishedAssetManifestStatus.COMPLETED and any(
            entry.status is not PublishedAssetStatus.PUBLISHED
            for entry in self.entries
        ):
            raise ValueError("completed published manifest requires active publications")
        if self.status is PublishedAssetManifestStatus.CLEANED and any(
            entry.status is not PublishedAssetStatus.REMOVED for entry in self.entries
        ):
            raise ValueError("cleaned published manifest requires removed entries")
        return self


class PublishableAsset(ContractModel):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    binary_asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str
    extension: str = Field(pattern=r"^[a-z0-9]{2,5}$")
    size_bytes: int = Field(gt=0, le=250_000_000)
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    source_manifest_kind: Literal["image_acquisition", "video_clip_generation"]
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_source_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="publishable_asset.metadata")
        if not isinstance(result, dict):
            raise ValueError("publishable asset metadata must be an object")
        _reject_embedded_urls(result)
        return result

    @model_validator(mode="after")
    def verify_content(self) -> PublishableAsset:
        import hashlib

        if self.content_type not in SUPPORTED_PUBLISHED_CONTENT_TYPES:
            raise ValueError("publishable content type is unsupported")
        if len(self.content) != self.size_bytes:
            raise ValueError("publishable content size differs")
        if hashlib.sha256(self.content).hexdigest() != self.source_hash:
            raise ValueError("publishable content checksum differs")
        return self


class AssetPublicationReceipt(ContractModel):
    publication_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    public_url: str = Field(repr=False, min_length=1, max_length=4096)
    url_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_at: datetime
    expires_at: datetime
    publisher: str = Field(min_length=1, max_length=100)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str
    size_bytes: int = Field(gt=0, le=250_000_000)

    @field_validator("published_at", "expires_at")
    @classmethod
    def aware_receipt_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("publication receipt time must be timezone-aware")
        return value

    @field_validator("public_url")
    @classmethod
    def safe_receipt_url(cls, value: str) -> str:
        return validate_public_https_url(value)

    @model_validator(mode="after")
    def valid_window(self) -> AssetPublicationReceipt:
        if self.expires_at <= self.published_at:
            raise ValueError("publication expiry must follow publication time")
        return self


def summarize_published_assets(
    entries: tuple[PublishedAsset, ...],
) -> PublishedAssetSummary:
    counts = dict.fromkeys(PublishedAssetStatus, 0)
    for entry in entries:
        counts[entry.status] += 1
    return PublishedAssetSummary(
        total=len(entries),
        not_published=counts[PublishedAssetStatus.NOT_PUBLISHED],
        publishing=counts[PublishedAssetStatus.PUBLISHING],
        published=counts[PublishedAssetStatus.PUBLISHED],
        failed=counts[PublishedAssetStatus.FAILED],
        expired=counts[PublishedAssetStatus.EXPIRED],
        removed=counts[PublishedAssetStatus.REMOVED],
    )


def replace_published_asset(
    manifest: PublishedAssetManifest,
    replacement: PublishedAsset,
    *,
    status: PublishedAssetManifestStatus | None = None,
    updated_at: datetime,
) -> PublishedAssetManifest:
    entries = tuple(
        replacement if entry.asset_id == replacement.asset_id else entry
        for entry in manifest.entries
    )
    if entries == manifest.entries and all(
        entry.asset_id != replacement.asset_id for entry in manifest.entries
    ):
        raise ValueError("replacement asset is absent from published manifest")
    payload = manifest.model_dump(mode="python")
    payload.update(
        {
            "entries": entries,
            "summary": summarize_published_assets(entries),
            "status": status or manifest.status,
            "updated_at": updated_at,
        }
    )
    return PublishedAssetManifest.model_validate(payload)


def validate_published_manifest_transition(
    previous: PublishedAssetManifest,
    current: PublishedAssetManifest,
) -> None:
    immutable_manifest = (
        previous.job_id == current.job_id
        and previous.attempt_number == current.attempt_number
        and previous.publisher == current.publisher
        and previous.source_manifests == current.source_manifests
        and previous.created_at == current.created_at
        and tuple(entry.asset_id for entry in previous.entries)
        == tuple(entry.asset_id for entry in current.entries)
    )
    if not immutable_manifest or current.updated_at < previous.updated_at:
        raise ValueError("published manifest immutable fields or time changed")
    allowed = {
        PublishedAssetStatus.NOT_PUBLISHED: {
            PublishedAssetStatus.NOT_PUBLISHED,
            PublishedAssetStatus.PUBLISHING,
            PublishedAssetStatus.REMOVED,
        },
        PublishedAssetStatus.PUBLISHING: {
            PublishedAssetStatus.PUBLISHING,
            PublishedAssetStatus.PUBLISHED,
            PublishedAssetStatus.FAILED,
            PublishedAssetStatus.NOT_PUBLISHED,
        },
        PublishedAssetStatus.PUBLISHED: {
            PublishedAssetStatus.PUBLISHED,
            PublishedAssetStatus.EXPIRED,
            PublishedAssetStatus.FAILED,
            PublishedAssetStatus.REMOVED,
        },
        PublishedAssetStatus.FAILED: {
            PublishedAssetStatus.FAILED,
            PublishedAssetStatus.PUBLISHING,
            PublishedAssetStatus.REMOVED,
        },
        PublishedAssetStatus.EXPIRED: {
            PublishedAssetStatus.EXPIRED,
            PublishedAssetStatus.PUBLISHING,
            PublishedAssetStatus.REMOVED,
        },
        PublishedAssetStatus.REMOVED: {PublishedAssetStatus.REMOVED},
    }
    immutable_entry_fields = (
        "asset_id",
        "binary_asset_id",
        "source_hash",
        "publisher",
        "content_type",
        "size_bytes",
        "metadata",
    )
    for old, new in zip(previous.entries, current.entries, strict=True):
        if new.status not in allowed[old.status]:
            raise ValueError(
                f"invalid publication transition {old.status} -> {new.status}"
            )
        if any(
            getattr(old, field) != getattr(new, field)
            for field in immutable_entry_fields
        ):
            raise ValueError("published asset immutable fields changed")
        if new.attempt_count < old.attempt_count:
            raise ValueError("publication attempt count cannot decrease")
        if (
            new.status is PublishedAssetStatus.PUBLISHING
            and old.status is not PublishedAssetStatus.PUBLISHING
            and new.attempt_count != old.attempt_count + 1
        ):
            raise ValueError("publishing transition must increment attempt count once")


def _url_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _reject_embedded_urls(value: Any) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _reject_embedded_urls(child)
    elif isinstance(value, list):
        for child in value:
            _reject_embedded_urls(child)
    elif isinstance(value, str) and "://" in value:
        raise ValueError("publication metadata cannot embed URLs")
