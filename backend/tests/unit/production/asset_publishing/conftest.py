"""Deterministic fixtures for secure asset publishing."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
    PublishedAssetMetadata,
    PublishedAssetSourceManifest,
    PublishedAssetStatus,
)
from backend.src.production.asset_publishing.url_validation import public_url_hash

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000005003")
CONTENT = b"verified-source-content"
SHA256 = hashlib.sha256(CONTENT).hexdigest()
SOURCE_SHA256 = "a" * 64


@pytest.fixture
def publishable_asset() -> PublishableAsset:
    return PublishableAsset(
        asset_id="publish-image-asset-001",
        binary_asset_id="image-asset-001",
        source_hash=SHA256,
        content_type="image/png",
        extension="png",
        size_bytes=len(CONTENT),
        content=CONTENT,
        source_manifest_kind="image_acquisition",
        source_manifest_sha256=SOURCE_SHA256,
        metadata={"visual_asset_id": "asset-001"},
    )


@pytest.fixture
def source_manifest() -> PublishedAssetSourceManifest:
    return PublishedAssetSourceManifest(
        kind="image_acquisition",
        schema_version="1.0.0",
        sha256=SOURCE_SHA256,
    )


def published_asset(
    *,
    status: PublishedAssetStatus = PublishedAssetStatus.PUBLISHED,
    expires_at: datetime | None = None,
) -> PublishedAsset:
    url = "https://assets.example.test/assets/pub-001.png"
    active = status is PublishedAssetStatus.PUBLISHED
    attempted = status is not PublishedAssetStatus.NOT_PUBLISHED
    has_provenance = status in {
        PublishedAssetStatus.PUBLISHING,
        PublishedAssetStatus.PUBLISHED,
        PublishedAssetStatus.FAILED,
        PublishedAssetStatus.EXPIRED,
        PublishedAssetStatus.REMOVED,
    }
    published_at = (
        min(NOW, expires_at - timedelta(minutes=1))
        if expires_at is not None
        else NOW
    )
    return PublishedAsset(
        asset_id="publish-image-asset-001",
        binary_asset_id="image-asset-001",
        source_hash=SHA256,
        published_at=published_at if has_provenance else None,
        expires_at=(
            expires_at or NOW + timedelta(minutes=15)
            if has_provenance
            else None
        ),
        publisher="filesystem",
        content_type="image/png",
        size_bytes=len(CONTENT),
        public_url=url if active else None,
        url_hash=public_url_hash(url) if active else None,
        status=status,
        attempt_count=1 if attempted else 0,
        error_code="publication_failed" if status is PublishedAssetStatus.FAILED else None,
        metadata=PublishedAssetMetadata(
            publication_id="pub-001",
            extension="png",
            source_manifest_kind="image_acquisition",
            source_manifest_sha256=SOURCE_SHA256,
            attributes={"visual_asset_id": "asset-001"},
        ),
    )


def receipt() -> AssetPublicationReceipt:
    url = "https://assets.example.test/assets/pub-001.png"
    return AssetPublicationReceipt(
        publication_id="pub-001",
        public_url=url,
        url_hash=public_url_hash(url),
        published_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        publisher="filesystem",
        source_hash=SHA256,
        content_type="image/png",
        size_bytes=len(CONTENT),
    )
