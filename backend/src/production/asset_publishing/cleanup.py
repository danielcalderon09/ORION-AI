"""Expiration-aware cleanup that clears URLs before removing published bytes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from backend.src.production.asset_publishing.models import (
    PublishedAsset,
    PublishedAssetManifest,
    PublishedAssetManifestStatus,
    PublishedAssetStatus,
    replace_published_asset,
)
from backend.src.production.asset_publishing.ports import (
    AssetPublisher,
    PublishedAssetManifestStore,
)


class PublishedAssetCleanupService:
    def __init__(
        self,
        *,
        publisher: AssetPublisher,
        manifests: PublishedAssetManifestStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._publisher = publisher
        self._manifests = manifests
        self._clock = clock

    async def cleanup(
        self, *, job_id: UUID, attempt_number: int
    ) -> PublishedAssetManifest | None:
        manifest = await self._manifests.read(
            job_id=job_id,
            attempt_number=attempt_number,
        )
        if manifest is None:
            return None
        now = self._aware_now()
        for entry in manifest.entries:
            if (
                entry.status is PublishedAssetStatus.PUBLISHED
                and entry.expires_at is not None
                and entry.expires_at <= now
            ):
                expired = entry.model_copy(
                    update={
                        "status": PublishedAssetStatus.EXPIRED,
                        "public_url": None,
                        "error_code": None,
                    }
                )
                manifest = await self._checkpoint(manifest, expired, now)
                entry = expired
            if entry.status is PublishedAssetStatus.EXPIRED:
                await self._publisher.delete(asset=entry)
                removed = entry.model_copy(
                    update={
                        "status": PublishedAssetStatus.REMOVED,
                        "public_url": None,
                        "error_code": None,
                    }
                )
                manifest = await self._checkpoint(manifest, removed, now)
        await self._publisher.cleanup_expired(now=now)
        return manifest

    async def _checkpoint(
        self,
        manifest: PublishedAssetManifest,
        entry: PublishedAsset,
        now: datetime,
    ) -> PublishedAssetManifest:
        entries = tuple(
            entry if item.asset_id == entry.asset_id else item
            for item in manifest.entries
        )
        status = (
            PublishedAssetManifestStatus.CLEANED
            if all(item.status is PublishedAssetStatus.REMOVED for item in entries)
            else PublishedAssetManifestStatus.IN_PROGRESS
        )
        current = replace_published_asset(
            manifest,
            entry,
            status=status,
            updated_at=now,
        )
        await self._manifests.checkpoint(previous=manifest, current=current)
        return current

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("asset cleanup clock must be timezone-aware")
        return value
