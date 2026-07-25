"""Read-only reconciliation and global configuration behavior."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.production.asset_publishing.configuration import (
    AssetPublishingConfiguration,
)
from backend.src.production.asset_publishing.exceptions import (
    AssetPublishingUnavailableError,
    PublishedAssetManifestCorruptError,
)
from backend.src.production.asset_publishing.manifest_store import (
    InMemoryPublishedAssetManifestStore,
)
from backend.src.production.asset_publishing.models import (
    PublishedAssetManifest,
    PublishedAssetManifestStatus,
    PublishedAssetStatus,
    summarize_published_assets,
)
from backend.src.production.asset_publishing.reconciliation import (
    PublishedAssetReconciler,
    PublishedAssetReconciliationIssueCode,
)
from backend.tests.unit.production.asset_publishing.conftest import (
    JOB_ID,
    NOW,
    published_asset,
)


class ReadOnlyPublisher:
    name = "filesystem"

    def __init__(self, *, exists: bool = True, error: bool = False) -> None:
        self.present = exists
        self.error = error
        self.mutations = 0

    async def publish(self, *, asset, expires_at):
        self.mutations += 1
        raise AssertionError("reconciliation must not publish")

    async def delete(self, *, asset):
        self.mutations += 1
        raise AssertionError("reconciliation must not delete")

    async def exists(self, *, asset):
        if self.error:
            raise AssetPublishingUnavailableError("fixture unavailable")
        return self.present

    async def get_public_url(self, *, asset):
        raise AssertionError("reconciliation does not expose URLs")

    async def cleanup_expired(self, *, now):
        self.mutations += 1
        raise AssertionError("reconciliation must not cleanup")

    async def close(self):
        return None


class Verifier:
    def __init__(self, *, manifest: bool = True, binary: bool = True) -> None:
        self.manifest = manifest
        self.binary = binary

    async def source_manifest_exists(self, *, job_id, source):
        return self.manifest

    async def binary_asset_exists(self, *, job_id, asset):
        return self.binary


class CorruptStore(InMemoryPublishedAssetManifestStore):
    async def list_manifests(self):
        raise PublishedAssetManifestCorruptError("fixture corrupt")


async def _store_manifest(source_manifest, *, entry=None):
    selected = entry or published_asset()
    manifest = PublishedAssetManifest(
        job_id=JOB_ID,
        attempt_number=1,
        publisher="filesystem",
        source_manifests=(source_manifest,),
        entries=(selected,),
        summary=summarize_published_assets((selected,)),
        status=(
            PublishedAssetManifestStatus.COMPLETED
            if selected.status is PublishedAssetStatus.PUBLISHED
            else PublishedAssetManifestStatus.IN_PROGRESS
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    store = InMemoryPublishedAssetManifestStore()
    await store.create(manifest)
    return store


async def test_healthy_publication_has_no_issues(source_manifest) -> None:
    publisher = ReadOnlyPublisher()
    issues = await PublishedAssetReconciler(
        manifests=await _store_manifest(source_manifest),
        publisher=publisher,
        verifier=Verifier(),
        clock=lambda: NOW,
    ).reconcile()
    assert issues == ()
    assert publisher.mutations == 0


@pytest.mark.parametrize(
    ("manifest_exists", "binary_exists", "expected"),
    [
        (False, True, PublishedAssetReconciliationIssueCode.ORPHAN_MANIFEST),
        (True, False, PublishedAssetReconciliationIssueCode.ORPHAN_BINARY_ASSET),
    ],
)
async def test_detects_orphan_sources(
    source_manifest,
    manifest_exists: bool,
    binary_exists: bool,
    expected: PublishedAssetReconciliationIssueCode,
) -> None:
    issues = await PublishedAssetReconciler(
        manifests=await _store_manifest(source_manifest),
        publisher=ReadOnlyPublisher(),
        verifier=Verifier(manifest=manifest_exists, binary=binary_exists),
        clock=lambda: NOW,
    ).reconcile()
    assert expected in {issue.code for issue in issues}


async def test_detects_expired_url(source_manifest) -> None:
    entry = published_asset(expires_at=NOW - timedelta(seconds=1))
    issues = await PublishedAssetReconciler(
        manifests=await _store_manifest(source_manifest, entry=entry),
        publisher=ReadOnlyPublisher(),
        clock=lambda: NOW,
    ).reconcile()
    assert PublishedAssetReconciliationIssueCode.EXPIRED_URL in {
        issue.code for issue in issues
    }


async def test_detects_missing_publication(source_manifest) -> None:
    issues = await PublishedAssetReconciler(
        manifests=await _store_manifest(source_manifest),
        publisher=ReadOnlyPublisher(exists=False),
        clock=lambda: NOW,
    ).reconcile()
    assert [issue.code for issue in issues] == [
        PublishedAssetReconciliationIssueCode.MISSING_PUBLICATION
    ]


async def test_publisher_error_is_reported_without_mutation(source_manifest) -> None:
    publisher = ReadOnlyPublisher(error=True)
    issues = await PublishedAssetReconciler(
        manifests=await _store_manifest(source_manifest),
        publisher=publisher,
        clock=lambda: NOW,
    ).reconcile()
    assert issues[0].code is PublishedAssetReconciliationIssueCode.PUBLISHER_ERROR
    assert publisher.mutations == 0


async def test_invalid_manifest_is_reported() -> None:
    issues = await PublishedAssetReconciler(
        manifests=CorruptStore(),
        publisher=ReadOnlyPublisher(),
        clock=lambda: NOW,
    ).reconcile()
    assert issues[0].code is PublishedAssetReconciliationIssueCode.INVALID_MANIFEST


@pytest.mark.parametrize("publisher", ["null", "filesystem"])
def test_configuration_accepts_closed_publishers(
    tmp_path: Path, publisher: str
) -> None:
    configuration = AssetPublishingConfiguration(
        publisher=publisher,
        public_root=tmp_path,
    )
    assert configuration.publisher == publisher


@pytest.mark.parametrize(
    "url",
    [
        "http://assets.example.test",
        "https://localhost",
        "file:///tmp/assets",
        "https://127.0.0.1/assets",
    ],
)
def test_configuration_rejects_unsafe_base_url(tmp_path: Path, url: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        AssetPublishingConfiguration(
            public_root=tmp_path,
            public_base_url=url,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lifetime_seconds", 29),
        ("lifetime_seconds", 86_401),
        ("max_asset_bytes", 0),
        ("max_asset_bytes", 250_000_001),
        ("max_manifest_bytes", 0),
        ("max_manifest_bytes", 16_000_001),
    ],
)
def test_configuration_limits_are_bounded(
    tmp_path: Path, field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        AssetPublishingConfiguration(
            public_root=tmp_path,
            **{field: value},
        )
