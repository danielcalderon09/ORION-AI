"""Checkpointed publication orchestration with recovery and duplicate prevention."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationIntegrityError,
    AssetPublishingError,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
    PublishedAssetManifest,
    PublishedAssetManifestStatus,
    PublishedAssetMetadata,
    PublishedAssetSourceManifest,
    PublishedAssetStatus,
    replace_published_asset,
    summarize_published_assets,
)
from backend.src.production.asset_publishing.ports import (
    AssetPublisher,
    PublishedAssetManifestStore,
)
from backend.src.production.asset_publishing.url_validation import (
    public_url_hash,
    validate_public_https_url,
)


class AssetPublishingService:
    def __init__(
        self,
        *,
        publisher: AssetPublisher,
        manifest_store: PublishedAssetManifestStore,
        lifetime_seconds: int = 900,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 30 <= lifetime_seconds <= 86_400:
            raise ValueError("publication lifetime is outside safe limits")
        self._publisher = publisher
        self._manifests = manifest_store
        self._lifetime = lifetime_seconds
        self._clock = clock

    async def publish(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        assets: tuple[PublishableAsset, ...],
        source_manifests: tuple[PublishedAssetSourceManifest, ...],
    ) -> PublishedAssetManifest:
        if not assets:
            raise AssetPublicationIntegrityError("no assets were supplied for publication")
        ordered_assets = tuple(sorted(assets, key=lambda item: item.asset_id))
        if len({asset.asset_id for asset in ordered_assets}) != len(ordered_assets):
            raise AssetPublicationIntegrityError("publishable asset IDs are duplicated")
        manifest = await self._manifests.read(
            job_id=job_id,
            attempt_number=attempt_number,
        )
        if manifest is None:
            manifest = self._initial_manifest(
                job_id=job_id,
                attempt_number=attempt_number,
                assets=ordered_assets,
                source_manifests=source_manifests,
            )
            await self._manifests.create(manifest)
        else:
            self._validate_resume(manifest, ordered_assets, source_manifests)

        sources = {asset.asset_id: asset for asset in ordered_assets}
        for entry in manifest.entries:
            source = sources[entry.asset_id]
            manifest = await self._recover_entry(manifest, entry, source)
            current = next(
                item for item in manifest.entries if item.asset_id == entry.asset_id
            )
            if current.status in {
                PublishedAssetStatus.PUBLISHED,
                PublishedAssetStatus.REMOVED,
                PublishedAssetStatus.EXPIRED,
            }:
                continue
            manifest = await self._publish_entry(manifest, current, source)
        final_status = _manifest_status(manifest.entries)
        if manifest.status is not final_status:
            payload = manifest.model_dump(mode="python")
            payload.update(
                status=final_status,
                updated_at=self._aware_now(),
            )
            final_manifest = PublishedAssetManifest.model_validate(payload)
            await self._manifests.checkpoint(
                previous=manifest,
                current=final_manifest,
            )
            manifest = final_manifest
        return manifest

    async def _recover_entry(
        self,
        manifest: PublishedAssetManifest,
        entry: PublishedAsset,
        source: PublishableAsset,
    ) -> PublishedAssetManifest:
        now = self._aware_now()
        if entry.status is PublishedAssetStatus.PUBLISHED:
            if entry.expires_at is not None and entry.expires_at <= now:
                expired = entry.model_copy(
                    update={
                        "status": PublishedAssetStatus.EXPIRED,
                        "public_url": None,
                        "error_code": None,
                    }
                )
                return await self._checkpoint(manifest, expired)
            if await self._publisher.exists(asset=entry):
                url = await self._publisher.get_public_url(asset=entry)
                if public_url_hash(url) != entry.url_hash:
                    failed = entry.model_copy(
                        update={
                            "status": PublishedAssetStatus.FAILED,
                            "public_url": None,
                            "error_code": "publication_url_mismatch",
                        }
                    )
                    return await self._checkpoint(manifest, failed)
                return manifest
            failed = entry.model_copy(
                update={
                    "status": PublishedAssetStatus.FAILED,
                    "public_url": None,
                    "error_code": "publication_missing",
                }
            )
            return await self._checkpoint(manifest, failed)
        if entry.status is PublishedAssetStatus.PUBLISHING:
            if await self._publisher.exists(asset=entry):
                url = await self._publisher.get_public_url(asset=entry)
                validate_public_https_url(url)
                recovered = entry.model_copy(
                    update={
                        "status": PublishedAssetStatus.PUBLISHED,
                        "public_url": url,
                        "url_hash": public_url_hash(url),
                        "error_code": None,
                    }
                )
                return await self._checkpoint(manifest, recovered)
            rolled_back = entry.model_copy(
                update={
                    "status": PublishedAssetStatus.NOT_PUBLISHED,
                    "published_at": None,
                    "expires_at": None,
                    "public_url": None,
                    "url_hash": None,
                    "error_code": None,
                }
            )
            return await self._checkpoint(manifest, rolled_back)
        if (
            entry.binary_asset_id != source.binary_asset_id
            or entry.source_hash != source.source_hash
            or entry.size_bytes != source.size_bytes
            or entry.content_type != source.content_type
        ):
            raise AssetPublicationIntegrityError(
                "publication source differs during recovery"
            )
        return manifest

    async def _publish_entry(
        self,
        manifest: PublishedAssetManifest,
        entry: PublishedAsset,
        source: PublishableAsset,
    ) -> PublishedAssetManifest:
        started_at = self._aware_now()
        publishing = entry.model_copy(
            update={
                "status": PublishedAssetStatus.PUBLISHING,
                "attempt_count": entry.attempt_count + 1,
                "published_at": started_at,
                "expires_at": started_at + timedelta(seconds=self._lifetime),
                "public_url": None,
                "url_hash": None,
                "error_code": None,
            }
        )
        manifest = await self._checkpoint(manifest, publishing)
        if publishing.expires_at is None:
            raise AssetPublicationIntegrityError(
                "publishing checkpoint lacks expiration"
            )
        try:
            receipt = await self._publisher.publish(
                asset=source,
                expires_at=publishing.expires_at,
            )
            self._validate_receipt(receipt, publishing)
        except asyncio.CancelledError:
            raise
        except AssetPublishingError as exc:
            failed = publishing.model_copy(
                update={
                    "status": PublishedAssetStatus.FAILED,
                    "public_url": None,
                    "url_hash": None,
                    "error_code": _error_code(exc),
                }
            )
            return await self._checkpoint(manifest, failed)
        stored = publishing.model_copy(
            update={
                "status": PublishedAssetStatus.PUBLISHED,
                "published_at": receipt.published_at,
                "expires_at": receipt.expires_at,
                "public_url": receipt.public_url,
                "url_hash": receipt.url_hash,
                "error_code": None,
            }
        )
        return await self._checkpoint(manifest, stored)

    def _initial_manifest(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        assets: tuple[PublishableAsset, ...],
        source_manifests: tuple[PublishedAssetSourceManifest, ...],
    ) -> PublishedAssetManifest:
        now = self._aware_now()
        entries = tuple(
            PublishedAsset(
                asset_id=asset.asset_id,
                binary_asset_id=asset.binary_asset_id,
                source_hash=asset.source_hash,
                publisher=self._publisher.name,
                content_type=asset.content_type,
                size_bytes=asset.size_bytes,
                status=PublishedAssetStatus.NOT_PUBLISHED,
                metadata=PublishedAssetMetadata(
                    publication_id=_publication_id(asset),
                    extension=asset.extension,
                    source_manifest_kind=asset.source_manifest_kind,
                    source_manifest_sha256=asset.source_manifest_sha256,
                    source_artifact_id=asset.source_artifact_id,
                    attributes=asset.metadata,
                ),
            )
            for asset in assets
        )
        return PublishedAssetManifest(
            job_id=job_id,
            attempt_number=attempt_number,
            publisher=self._publisher.name,
            source_manifests=tuple(
                sorted(source_manifests, key=lambda item: item.kind)
            ),
            entries=entries,
            summary=summarize_published_assets(entries),
            status=PublishedAssetManifestStatus.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            metadata={"checkpointed": True},
        )

    def _validate_resume(
        self,
        manifest: PublishedAssetManifest,
        assets: tuple[PublishableAsset, ...],
        source_manifests: tuple[PublishedAssetSourceManifest, ...],
    ) -> None:
        if (
            manifest.publisher != self._publisher.name
            or manifest.source_manifests
            != tuple(sorted(source_manifests, key=lambda item: item.kind))
            or tuple(entry.asset_id for entry in manifest.entries)
            != tuple(asset.asset_id for asset in assets)
        ):
            raise AssetPublicationIntegrityError(
                "existing published manifest has different inputs"
            )
        for entry, asset in zip(manifest.entries, assets, strict=True):
            if (
                entry.binary_asset_id != asset.binary_asset_id
                or entry.source_hash != asset.source_hash
                or entry.content_type != asset.content_type
                or entry.size_bytes != asset.size_bytes
            ):
                raise AssetPublicationIntegrityError(
                    "existing published manifest source differs"
                )

    @staticmethod
    def _validate_receipt(
        receipt: AssetPublicationReceipt,
        expected: PublishedAsset,
    ) -> None:
        validate_public_https_url(receipt.public_url)
        if (
            receipt.publication_id != expected.metadata.publication_id
            or receipt.publisher != expected.publisher
            or receipt.source_hash != expected.source_hash
            or receipt.content_type != expected.content_type
            or receipt.size_bytes != expected.size_bytes
            or receipt.url_hash != public_url_hash(receipt.public_url)
        ):
            raise AssetPublicationIntegrityError(
                "publisher receipt differs from requested asset"
            )

    async def _checkpoint(
        self,
        manifest: PublishedAssetManifest,
        entry: PublishedAsset,
    ) -> PublishedAssetManifest:
        current = replace_published_asset(
            manifest,
            entry,
            status=_manifest_status(
                tuple(
                    entry if item.asset_id == entry.asset_id else item
                    for item in manifest.entries
                )
            ),
            updated_at=self._aware_now(),
        )
        await self._manifests.checkpoint(previous=manifest, current=current)
        return current

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("asset publishing clock must be timezone-aware")
        return value


def _publication_id(asset: PublishableAsset) -> str:
    import hashlib

    digest = hashlib.sha256(
        f"{asset.binary_asset_id}:{asset.source_hash}".encode()
    ).hexdigest()[:32]
    return f"pub-{digest}"


def _manifest_status(
    entries: tuple[PublishedAsset, ...],
) -> PublishedAssetManifestStatus:
    if all(entry.status is PublishedAssetStatus.PUBLISHED for entry in entries):
        return PublishedAssetManifestStatus.COMPLETED
    if all(entry.status is PublishedAssetStatus.REMOVED for entry in entries):
        return PublishedAssetManifestStatus.CLEANED
    if any(entry.status is PublishedAssetStatus.FAILED for entry in entries):
        return PublishedAssetManifestStatus.FAILED
    return PublishedAssetManifestStatus.IN_PROGRESS


def _error_code(exc: AssetPublishingError) -> str:
    name = type(exc).__name__
    code = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            code.append("_")
        code.append(character.lower())
    return "".join(code)[:100]
