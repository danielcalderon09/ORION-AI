"""Publisher abstraction and development filesystem adapter tests."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationConflictError,
    AssetPublicationIntegrityError,
    AssetPublicationNotFoundError,
    AssetPublishingConfigurationError,
    AssetPublishingUnavailableError,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishedAsset,
    PublishedAssetMetadata,
    PublishedAssetStatus,
)
from backend.src.production.asset_publishing.publishers import (
    FilesystemPublisher,
    FutureCloudPublisher,
    NullPublisher,
)
from backend.src.production.asset_publishing.url_validation import public_url_hash
from backend.tests.unit.production.asset_publishing.conftest import NOW


def _publisher(root: Path, *, now=NOW) -> FilesystemPublisher:
    return FilesystemPublisher(
        public_root=root,
        public_base_url="https://assets.example.test",
        max_asset_bytes=1_000_000,
        clock=lambda: now,
    )


def _entry(receipt: AssetPublicationReceipt, source) -> PublishedAsset:
    return PublishedAsset(
        asset_id=source.asset_id,
        binary_asset_id=source.binary_asset_id,
        source_hash=source.source_hash,
        published_at=receipt.published_at,
        expires_at=receipt.expires_at,
        publisher=receipt.publisher,
        content_type=receipt.content_type,
        size_bytes=receipt.size_bytes,
        public_url=receipt.public_url,
        url_hash=receipt.url_hash,
        status=PublishedAssetStatus.PUBLISHED,
        attempt_count=1,
        metadata=PublishedAssetMetadata(
            publication_id=receipt.publication_id,
            extension=source.extension,
            source_manifest_kind=source.source_manifest_kind,
            source_manifest_sha256=source.source_manifest_sha256,
            attributes=source.metadata,
        ),
    )


async def test_filesystem_publish_writes_bytes_and_sidecar(
    tmp_path: Path, publishable_asset
) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    binary = tmp_path / f"{receipt.publication_id}.png"
    sidecar = tmp_path / f"{receipt.publication_id}.publication.json"
    assert binary.read_bytes() == publishable_asset.content
    assert json.loads(sidecar.read_text(encoding="utf-8"))["source_hash"] == (
        publishable_asset.source_hash
    )


async def test_publish_is_idempotent(tmp_path: Path, publishable_asset) -> None:
    publisher = _publisher(tmp_path)
    first = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    second = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    assert second == first
    assert len(list(tmp_path.glob("*.png"))) == 1


@pytest.mark.parametrize("missing", ["binary", "sidecar"])
async def test_publish_recovers_interrupted_atomic_pair(
    tmp_path: Path, publishable_asset, missing: str
) -> None:
    publisher = _publisher(tmp_path)
    first = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    binary = tmp_path / f"{first.publication_id}.png"
    sidecar = tmp_path / f"{first.publication_id}.publication.json"
    (binary if missing == "binary" else sidecar).unlink()
    recovered = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    assert binary.read_bytes() == publishable_asset.content
    assert sidecar.is_file()
    assert recovered.source_hash == publishable_asset.source_hash


async def test_exists_and_get_public_url(tmp_path: Path, publishable_asset) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    entry = _entry(receipt, publishable_asset)
    assert await publisher.exists(asset=entry)
    assert await publisher.get_public_url(asset=entry) == receipt.public_url


async def test_delete_is_idempotent(tmp_path: Path, publishable_asset) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    entry = _entry(receipt, publishable_asset)
    await publisher.delete(asset=entry)
    await publisher.delete(asset=entry)
    assert not await publisher.exists(asset=entry)


async def test_delete_absent_does_not_create_root(
    tmp_path: Path, publishable_asset
) -> None:
    root = tmp_path / "absent"
    publisher = _publisher(root)
    fake = PublishedAsset(
        asset_id=publishable_asset.asset_id,
        binary_asset_id=publishable_asset.binary_asset_id,
        source_hash=publishable_asset.source_hash,
        published_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        publisher="filesystem",
        content_type="image/png",
        size_bytes=publishable_asset.size_bytes,
        public_url=None,
        url_hash=None,
        status=PublishedAssetStatus.EXPIRED,
        attempt_count=1,
        metadata=PublishedAssetMetadata(
            publication_id="pub-absent",
            extension="png",
            source_manifest_kind="image_acquisition",
            source_manifest_sha256=publishable_asset.source_manifest_sha256,
        ),
    )
    await publisher.delete(asset=fake)
    assert not root.exists()


async def test_cleanup_removes_only_expired(tmp_path: Path, publishable_asset) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=5),
    )
    assert await publisher.cleanup_expired(now=NOW) == ()
    assert await publisher.cleanup_expired(
        now=NOW + timedelta(minutes=6)
    ) == (receipt.publication_id,)


@pytest.mark.parametrize("mutation", ["binary", "sidecar", "missing_binary"])
async def test_integrity_drift_is_not_reported_as_existing(
    tmp_path: Path, publishable_asset, mutation: str
) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    entry = _entry(receipt, publishable_asset)
    binary = tmp_path / f"{receipt.publication_id}.png"
    sidecar = tmp_path / f"{receipt.publication_id}.publication.json"
    if mutation == "binary":
        binary.write_bytes(b"corrupt")
    elif mutation == "sidecar":
        sidecar.write_text("{}", encoding="utf-8")
    else:
        binary.unlink()
    assert not await publisher.exists(asset=entry)


async def test_hard_link_is_rejected(tmp_path: Path, publishable_asset) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    entry = _entry(receipt, publishable_asset)
    binary = tmp_path / f"{receipt.publication_id}.png"
    os.link(binary, tmp_path / "alias.png")
    assert not await publisher.exists(asset=entry)


async def test_existing_incompatible_sidecar_conflicts(
    tmp_path: Path, publishable_asset
) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    sidecar = tmp_path / f"{receipt.publication_id}.publication.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["source_hash"] = "f" * 64
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AssetPublicationConflictError):
        await publisher.publish(
            asset=publishable_asset,
            expires_at=NOW + timedelta(minutes=15),
        )


async def test_invalid_sidecar_raises_integrity(
    tmp_path: Path, publishable_asset
) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    sidecar = tmp_path / f"{receipt.publication_id}.publication.json"
    sidecar.write_bytes(b"{invalid")
    with pytest.raises(AssetPublicationIntegrityError):
        await publisher.publish(
            asset=publishable_asset,
            expires_at=NOW + timedelta(minutes=15),
        )


async def test_missing_sidecar_get_url_fails(tmp_path: Path, publishable_asset) -> None:
    publisher = _publisher(tmp_path)
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=NOW + timedelta(minutes=15),
    )
    entry = _entry(receipt, publishable_asset)
    (tmp_path / f"{receipt.publication_id}.publication.json").unlink()
    with pytest.raises(AssetPublicationNotFoundError):
        await publisher.get_public_url(asset=entry)


async def test_closed_filesystem_publisher_fails_closed(
    tmp_path: Path, publishable_asset
) -> None:
    publisher = _publisher(tmp_path)
    await publisher.close()
    with pytest.raises(AssetPublishingConfigurationError):
        await publisher.publish(
            asset=publishable_asset,
            expires_at=NOW + timedelta(minutes=15),
        )


@pytest.mark.parametrize(
    "content_type",
    ["image/png", "image/jpeg", "image/webp", "video/mp4"],
)
async def test_content_type_extension_mapping(
    tmp_path: Path, publishable_asset, content_type: str
) -> None:
    extensions = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "video/mp4": "mp4",
    }
    source = publishable_asset.model_copy(
        update={"content_type": content_type, "extension": extensions[content_type]}
    )
    publisher = _publisher(tmp_path / extensions[content_type])
    receipt = await publisher.publish(
        asset=source,
        expires_at=NOW + timedelta(minutes=15),
    )
    assert (tmp_path / extensions[content_type] / (
        f"{receipt.publication_id}.{extensions[content_type]}"
    )).is_file()


@pytest.mark.parametrize("max_bytes", [0, 250_000_001])
def test_filesystem_size_limits_are_closed(tmp_path: Path, max_bytes: int) -> None:
    with pytest.raises(AssetPublishingConfigurationError):
        FilesystemPublisher(
            public_root=tmp_path,
            public_base_url="https://assets.example.test",
            max_asset_bytes=max_bytes,
        )


async def test_null_publisher_fails_publish_and_never_exists(
    publishable_asset,
) -> None:
    publisher = NullPublisher()
    with pytest.raises(AssetPublishingUnavailableError):
        await publisher.publish(
            asset=publishable_asset,
            expires_at=NOW + timedelta(minutes=15),
        )
    entry = PublishedAsset.model_construct(metadata=None)
    assert not await publisher.exists(asset=entry)
    assert await publisher.cleanup_expired(now=NOW) == ()
    await publisher.close()


@pytest.mark.parametrize(
    "operation",
    ["publish", "delete", "exists", "get_public_url", "cleanup_expired"],
)
async def test_future_cloud_placeholder_is_unavailable(
    operation: str, publishable_asset
) -> None:
    publisher = FutureCloudPublisher()
    entry = PublishedAsset.model_construct(metadata=None)
    with pytest.raises(AssetPublishingUnavailableError):
        if operation == "publish":
            await publisher.publish(
                asset=publishable_asset,
                expires_at=NOW + timedelta(minutes=15),
            )
        elif operation == "delete":
            await publisher.delete(asset=entry)
        elif operation == "exists":
            await publisher.exists(asset=entry)
        elif operation == "get_public_url":
            await publisher.get_public_url(asset=entry)
        else:
            await publisher.cleanup_expired(now=NOW)
    await publisher.close()


def test_receipt_never_contains_credentials(publishable_asset) -> None:
    url = "https://assets.example.test/sensitive-marker"
    result = AssetPublicationReceipt(
        publication_id="pub-a",
        public_url=url,
        url_hash=public_url_hash(url),
        published_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        publisher="filesystem",
        source_hash=publishable_asset.source_hash,
        content_type=publishable_asset.content_type,
        size_bytes=publishable_asset.size_bytes,
    )
    assert "sensitive-marker" not in repr(result)
