"""Adapters accept only verified bytes from completed durable manifests."""

from pathlib import Path

import pytest

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationIntegrityError,
)
from backend.src.production.asset_publishing.sources import (
    ManifestPublishableAssetCollector,
)
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
    ImageAcquisitionManifestStatus,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    JOB_ID,
    durable_source,
)


class UnusedVideoStore:
    async def write(self, **kwargs):
        raise AssertionError("image collection must not use video storage")

    async def read_verified(self, **kwargs):
        raise AssertionError("image collection must not use video storage")

    async def resolve(self, **kwargs):
        raise AssertionError("image collection must not use video storage")


async def test_collects_verified_image_bytes_from_completed_manifest(
    tmp_path: Path,
) -> None:
    source, store, _ = await durable_source(tmp_path)
    result = await ManifestPublishableAssetCollector(
        binary_assets=store,
        video_clips=UnusedVideoStore(),
    ).collect_images(
        job_id=JOB_ID,
        manifest=source.manifest,
        manifest_sha256=source.sha256,
        manifest_artifact_id=source.artifact_id,
    )
    assert len(result) == 1
    assert result[0].content == source.source_images[0].content
    assert result[0].source_hash == source.source_images[0].sha256


async def test_rejects_incomplete_image_manifest(tmp_path: Path) -> None:
    source, store, _ = await durable_source(tmp_path)
    incomplete = source.manifest.model_copy(
        update={"status": ImageAcquisitionManifestStatus.IN_PROGRESS}
    )
    with pytest.raises(AssetPublicationIntegrityError):
        await ManifestPublishableAssetCollector(
            binary_assets=store,
            video_clips=UnusedVideoStore(),
        ).collect_images(
            job_id=JOB_ID,
            manifest=incomplete,
            manifest_sha256=source.sha256,
        )


async def test_rejects_unstored_image_entry(tmp_path: Path) -> None:
    source, store, _ = await durable_source(tmp_path)
    entry = source.manifest.entries[0].model_copy(
        update={"status": ImageAcquisitionEntryStatus.FAILED_PERMANENT}
    )
    incomplete = source.manifest.model_copy(update={"entries": (entry,)})
    with pytest.raises(AssetPublicationIntegrityError):
        await ManifestPublishableAssetCollector(
            binary_assets=store,
            video_clips=UnusedVideoStore(),
        ).collect_images(
            job_id=JOB_ID,
            manifest=incomplete,
            manifest_sha256=source.sha256,
        )


async def test_rejects_binary_checksum_drift(tmp_path: Path) -> None:
    source, store, _ = await durable_source(tmp_path)
    entry = source.manifest.entries[0].model_copy(update={"sha256": "f" * 64})
    drifted = source.manifest.model_copy(update={"entries": (entry,)})
    with pytest.raises(AssetPublicationIntegrityError):
        await ManifestPublishableAssetCollector(
            binary_assets=store,
            video_clips=UnusedVideoStore(),
        ).collect_images(
            job_id=JOB_ID,
            manifest=drifted,
            manifest_sha256=source.sha256,
        )
