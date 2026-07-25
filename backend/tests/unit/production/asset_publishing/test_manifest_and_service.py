"""Durable manifests, checkpoints, recovery, retry, and cleanup."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from pathlib import Path

import pytest

from backend.src.production.asset_publishing.cleanup import (
    PublishedAssetCleanupService,
)
from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationIntegrityError,
    AssetPublishingUnavailableError,
    PublishedAssetManifestConflictError,
    PublishedAssetManifestCorruptError,
)
from backend.src.production.asset_publishing.manifest_store import (
    InMemoryPublishedAssetManifestStore,
    LocalPublishedAssetManifestStore,
    published_asset_manifest_relative_path,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishedAssetManifestStatus,
    PublishedAssetStatus,
)
from backend.src.production.asset_publishing.serialization import (
    deserialize_published_asset_manifest,
    serialize_published_asset_manifest,
)
from backend.src.production.asset_publishing.service import AssetPublishingService
from backend.src.production.asset_publishing.url_validation import public_url_hash
from backend.tests.unit.production.asset_publishing.conftest import JOB_ID, NOW


class RecordingPublisher:
    name = "recording"

    def __init__(self, *, fail: bool = False, cancel: bool = False) -> None:
        self.fail = fail
        self.cancel = cancel
        self.calls = 0
        self.deleted: list[str] = []
        self.receipts: dict[str, AssetPublicationReceipt] = {}
        self.closed = False

    async def publish(self, *, asset, expires_at):
        self.calls += 1
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail:
            raise AssetPublishingUnavailableError("fixture unavailable")
        publication_id = "pub-" + hashlib.sha256(
            f"{asset.binary_asset_id}:{asset.source_hash}".encode()
        ).hexdigest()[:32]
        url = f"https://assets.example.test/assets/{publication_id}.{asset.extension}"
        receipt = AssetPublicationReceipt(
            publication_id=publication_id,
            public_url=url,
            url_hash=public_url_hash(url),
            published_at=NOW,
            expires_at=expires_at,
            publisher=self.name,
            source_hash=asset.source_hash,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
        )
        self.receipts[publication_id] = receipt
        return receipt

    async def delete(self, *, asset):
        self.deleted.append(asset.metadata.publication_id)
        self.receipts.pop(asset.metadata.publication_id, None)

    async def exists(self, *, asset):
        return asset.metadata.publication_id in self.receipts

    async def get_public_url(self, *, asset):
        return self.receipts[asset.metadata.publication_id].public_url

    async def cleanup_expired(self, *, now):
        expired = tuple(
            key
            for key, value in self.receipts.items()
            if value.expires_at <= now
        )
        for key in expired:
            self.receipts.pop(key)
        return expired

    async def close(self):
        self.closed = True


async def _publish(
    publisher,
    store,
    publishable_asset,
    source_manifest,
    *,
    attempt: int = 1,
    now=NOW,
):
    return await AssetPublishingService(
        publisher=publisher,
        manifest_store=store,
        lifetime_seconds=900,
        clock=lambda: now,
    ).publish(
        job_id=JOB_ID,
        attempt_number=attempt,
        assets=(publishable_asset,),
        source_manifests=(source_manifest,),
    )


async def test_success_checkpoints_each_state(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    result = await _publish(publisher, store, publishable_asset, source_manifest)
    assert result.status is PublishedAssetManifestStatus.COMPLETED
    assert result.entries[0].status is PublishedAssetStatus.PUBLISHED
    assert result.entries[0].attempt_count == 1
    assert publisher.calls == 1
    assert store.checkpoints == 3


async def test_existing_published_asset_prevents_duplicate(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    first = await _publish(publisher, store, publishable_asset, source_manifest)
    second = await _publish(publisher, store, publishable_asset, source_manifest)
    assert second == first
    assert publisher.calls == 1


async def test_failed_publication_is_checkpointed_and_retryable(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher(fail=True)
    store = InMemoryPublishedAssetManifestStore()
    failed = await _publish(publisher, store, publishable_asset, source_manifest)
    assert failed.status is PublishedAssetManifestStatus.FAILED
    assert failed.entries[0].status is PublishedAssetStatus.FAILED
    publisher.fail = False
    recovered = await _publish(publisher, store, publishable_asset, source_manifest)
    assert recovered.status is PublishedAssetManifestStatus.COMPLETED
    assert recovered.entries[0].attempt_count == 2


async def test_cancel_propagates_and_leaves_publishing_checkpoint(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher(cancel=True)
    store = InMemoryPublishedAssetManifestStore()
    with pytest.raises(asyncio.CancelledError):
        await _publish(publisher, store, publishable_asset, source_manifest)
    manifest = await store.read(job_id=JOB_ID, attempt_number=1)
    assert manifest is not None
    assert manifest.entries[0].status is PublishedAssetStatus.PUBLISHING


async def test_interrupted_publish_rolls_back_then_retries(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher(cancel=True)
    store = InMemoryPublishedAssetManifestStore()
    with pytest.raises(asyncio.CancelledError):
        await _publish(publisher, store, publishable_asset, source_manifest)
    publisher.cancel = False
    result = await _publish(publisher, store, publishable_asset, source_manifest)
    assert result.entries[0].status is PublishedAssetStatus.PUBLISHED
    assert result.entries[0].attempt_count == 2


async def test_interrupted_publish_recovers_existing_without_duplicate(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher(cancel=True)
    store = InMemoryPublishedAssetManifestStore()
    with pytest.raises(asyncio.CancelledError):
        await _publish(publisher, store, publishable_asset, source_manifest)
    manifest = await store.read(job_id=JOB_ID, attempt_number=1)
    assert manifest is not None
    entry = manifest.entries[0]
    publisher.cancel = False
    receipt = await publisher.publish(
        asset=publishable_asset,
        expires_at=entry.expires_at,
    )
    calls_before = publisher.calls
    result = await _publish(publisher, store, publishable_asset, source_manifest)
    assert result.entries[0].public_url == receipt.public_url
    assert publisher.calls == calls_before


@pytest.mark.parametrize(
    "field",
    ["binary_asset_id", "source_hash", "content_type", "size_bytes"],
)
async def test_resume_rejects_changed_source(
    publishable_asset, source_manifest, field: str
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    await _publish(publisher, store, publishable_asset, source_manifest)
    replacements = {
        "binary_asset_id": "image-different",
        "source_hash": hashlib.sha256(b"different").hexdigest(),
        "content_type": "image/jpeg",
        "size_bytes": publishable_asset.size_bytes + 1,
    }
    changed = publishable_asset.model_copy(update={field: replacements[field]})
    with pytest.raises(AssetPublicationIntegrityError):
        await _publish(publisher, store, changed, source_manifest)


async def test_source_manifest_change_rejects_resume(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    await _publish(publisher, store, publishable_asset, source_manifest)
    changed = source_manifest.model_copy(update={"sha256": "b" * 64})
    with pytest.raises(AssetPublicationIntegrityError):
        await _publish(publisher, store, publishable_asset, changed)


async def test_cleanup_clears_url_then_removes_bytes(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    await _publish(publisher, store, publishable_asset, source_manifest)
    cleanup = PublishedAssetCleanupService(
        publisher=publisher,
        manifests=store,
        clock=lambda: NOW + timedelta(minutes=16),
    )
    result = await cleanup.cleanup(job_id=JOB_ID, attempt_number=1)
    assert result is not None
    assert result.status is PublishedAssetManifestStatus.CLEANED
    assert result.entries[0].status is PublishedAssetStatus.REMOVED
    assert result.entries[0].public_url is None
    assert publisher.deleted


async def test_cleanup_before_expiry_is_noop(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    first = await _publish(publisher, store, publishable_asset, source_manifest)
    result = await PublishedAssetCleanupService(
        publisher=publisher,
        manifests=store,
        clock=lambda: NOW,
    ).cleanup(job_id=JOB_ID, attempt_number=1)
    assert result == first
    assert not publisher.deleted


async def test_cleanup_missing_manifest_returns_none() -> None:
    result = await PublishedAssetCleanupService(
        publisher=RecordingPublisher(),
        manifests=InMemoryPublishedAssetManifestStore(),
        clock=lambda: NOW,
    ).cleanup(job_id=JOB_ID, attempt_number=99)
    assert result is None


def test_contractual_manifest_path() -> None:
    assert published_asset_manifest_relative_path(
        job_id=JOB_ID,
        attempt_number=2,
    ) == (
        f"production/{JOB_ID}/asset_publishing/attempt-2/"
        "published-assets-manifest.json"
    )


@pytest.mark.parametrize("attempt", [0, -1, -100])
def test_manifest_path_rejects_invalid_attempt(attempt: int) -> None:
    with pytest.raises(ValueError):
        published_asset_manifest_relative_path(
            job_id=JOB_ID,
            attempt_number=attempt,
        )


async def test_local_manifest_round_trip(
    tmp_path: Path, publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = LocalPublishedAssetManifestStore(tmp_path)
    expected = await _publish(publisher, store, publishable_asset, source_manifest)
    actual = await store.read(job_id=JOB_ID, attempt_number=1)
    assert actual == expected
    assert len(await store.list_manifests()) == 1


async def test_local_manifest_compare_and_swap(
    tmp_path: Path, publishable_asset, source_manifest
) -> None:
    store = LocalPublishedAssetManifestStore(tmp_path)
    expected = await _publish(
        RecordingPublisher(),
        store,
        publishable_asset,
        source_manifest,
    )
    with pytest.raises(PublishedAssetManifestConflictError):
        await store.checkpoint(previous=expected.model_copy(update={"metadata": {}}), current=expected)


async def test_local_manifest_rejects_corruption(
    tmp_path: Path, publishable_asset, source_manifest
) -> None:
    store = LocalPublishedAssetManifestStore(tmp_path)
    await _publish(RecordingPublisher(), store, publishable_asset, source_manifest)
    target = tmp_path.joinpath(
        *published_asset_manifest_relative_path(
            job_id=JOB_ID,
            attempt_number=1,
        ).split("/")
    )
    target.write_bytes(b"{invalid")
    with pytest.raises(PublishedAssetManifestCorruptError):
        await store.read(job_id=JOB_ID, attempt_number=1)


@pytest.mark.parametrize(
    "content",
    [
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1,"x":2}',
        b"\xff",
        b"",
    ],
)
def test_manifest_deserializer_rejects_noncanonical_input(content: bytes) -> None:
    with pytest.raises(PublishedAssetManifestCorruptError):
        deserialize_published_asset_manifest(content)


async def test_serialization_is_canonical(
    publishable_asset, source_manifest
) -> None:
    manifest = await _publish(
        RecordingPublisher(),
        InMemoryPublishedAssetManifestStore(),
        publishable_asset,
        source_manifest,
    )
    first = serialize_published_asset_manifest(manifest)
    second = serialize_published_asset_manifest(manifest)
    assert first == second
    assert first.endswith(b"\n")


async def test_duplicate_concurrent_execution_does_not_duplicate_publish(
    publishable_asset, source_manifest
) -> None:
    publisher = RecordingPublisher()
    store = InMemoryPublishedAssetManifestStore()
    results = await asyncio.gather(
        _publish(publisher, store, publishable_asset, source_manifest),
        _publish(publisher, store, publishable_asset, source_manifest),
        return_exceptions=True,
    )
    assert publisher.calls == 1
    assert all(not isinstance(item, Exception) for item in results)
