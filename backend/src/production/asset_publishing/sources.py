"""Adapters from durable image/video manifests to verified publishable bytes."""

from __future__ import annotations

from uuid import UUID

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationIntegrityError,
)
from backend.src.production.asset_publishing.models import PublishableAsset
from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.models import (
    ProductionBinaryAssetReference,
)
from backend.src.production.binary_assets.ports import BinaryAssetReader
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
    ImageAcquisitionManifestStatus,
    ProductionImageAcquisitionManifest,
)
from backend.src.production.video_clip_generation.exceptions import (
    VideoClipGenerationError,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipManifest,
    VideoClipEntryStatus,
    VideoClipManifestStatus,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipBinaryStore,
)


class ManifestPublishableAssetCollector:
    """Reads only assets referenced by completed, already-validated manifests."""

    def __init__(
        self,
        *,
        binary_assets: BinaryAssetReader,
        video_clips: VideoClipBinaryStore,
    ) -> None:
        self._binary_assets = binary_assets
        self._video_clips = video_clips

    async def collect_images(
        self,
        *,
        job_id: UUID,
        manifest: ProductionImageAcquisitionManifest,
        manifest_sha256: str,
        manifest_artifact_id: UUID | None = None,
    ) -> tuple[PublishableAsset, ...]:
        if manifest.status is not ImageAcquisitionManifestStatus.COMPLETED:
            raise AssetPublicationIntegrityError(
                "image acquisition manifest is not completed"
            )
        result: list[PublishableAsset] = []
        for entry in manifest.entries:
            if entry.status is not ImageAcquisitionEntryStatus.STORED:
                raise AssetPublicationIntegrityError(
                    "image manifest contains an unstored entry"
                )
            required = (
                entry.binary_asset_id,
                entry.storage_path,
                entry.mime_type,
                entry.extension,
                entry.sha256,
                entry.size_bytes,
                entry.width,
                entry.height,
            )
            if any(value is None for value in required):
                raise AssetPublicationIntegrityError(
                    "stored image entry lacks binary metadata"
                )
            if (
                entry.binary_asset_id is None
                or entry.storage_path is None
                or entry.mime_type is None
                or entry.extension is None
                or entry.sha256 is None
                or entry.size_bytes is None
                or entry.width is None
                or entry.height is None
            ):
                raise AssetPublicationIntegrityError(
                    "stored image entry lacks binary metadata"
                )
            reference = ProductionBinaryAssetReference(
                asset_id=entry.binary_asset_id,
                job_id=job_id,
                storage_path=entry.storage_path,
                mime_type=entry.mime_type,
                extension=entry.extension,
                sha256=entry.sha256,
                size_bytes=entry.size_bytes,
                width=entry.width,
                height=entry.height,
            )
            try:
                read = await self._binary_assets.read(reference=reference)
            except BinaryAssetError as exc:
                raise AssetPublicationIntegrityError(
                    "image binary differs from its durable manifest"
                ) from exc
            result.append(
                PublishableAsset(
                    asset_id=f"publish-{entry.binary_asset_id}",
                    binary_asset_id=entry.binary_asset_id,
                    source_hash=entry.sha256,
                    content_type=entry.mime_type,
                    extension=entry.extension,
                    size_bytes=entry.size_bytes,
                    content=read.content,
                    source_manifest_kind="image_acquisition",
                    source_manifest_sha256=manifest_sha256,
                    source_artifact_id=entry.binary_artifact_id,
                    metadata={
                        "visual_asset_id": entry.visual_asset_id,
                        "scene_id": entry.source_scene_id,
                        "shot_id": entry.source_shot_id,
                    },
                )
            )
        return tuple(sorted(result, key=lambda item: item.asset_id))

    async def collect_video_clips(
        self,
        *,
        job_id: UUID,
        manifest: ProductionVideoClipManifest,
        manifest_sha256: str,
    ) -> tuple[PublishableAsset, ...]:
        if manifest.status is not VideoClipManifestStatus.COMPLETED:
            raise AssetPublicationIntegrityError(
                "video clip manifest is not completed"
            )
        result: list[PublishableAsset] = []
        for entry in manifest.entries:
            if (
                entry.status is not VideoClipEntryStatus.STORED
                or entry.video_binary_asset_id is None
                or entry.sha256 is None
                or entry.size_bytes is None
                or entry.mime_type is None
                or entry.extension is None
            ):
                raise AssetPublicationIntegrityError(
                    "video manifest contains an incomplete clip"
                )
            try:
                read = await self._video_clips.resolve(
                    job_id=job_id,
                    visual_asset_id=entry.visual_asset_id,
                )
            except VideoClipGenerationError as exc:
                raise AssetPublicationIntegrityError(
                    "video binary differs from its durable manifest"
                ) from exc
            if (
                read.asset.asset_id != entry.video_binary_asset_id
                or read.asset.sha256 != entry.sha256
                or read.asset.size_bytes != entry.size_bytes
                or read.asset.mime_type != entry.mime_type
            ):
                raise AssetPublicationIntegrityError(
                    "video clip differs from durable manifest"
                )
            result.append(
                PublishableAsset(
                    asset_id=f"publish-{entry.video_binary_asset_id}",
                    binary_asset_id=entry.video_binary_asset_id,
                    source_hash=entry.sha256,
                    content_type=entry.mime_type,
                    extension=entry.extension,
                    size_bytes=entry.size_bytes,
                    content=read.content,
                    source_manifest_kind="video_clip_generation",
                    source_manifest_sha256=manifest_sha256,
                    source_artifact_id=entry.video_artifact_id,
                    metadata={
                        "visual_asset_id": entry.visual_asset_id,
                        "scene_id": entry.source_scene_id,
                        "shot_id": entry.source_shot_id,
                    },
                )
            )
        return tuple(sorted(result, key=lambda item: item.asset_id))
