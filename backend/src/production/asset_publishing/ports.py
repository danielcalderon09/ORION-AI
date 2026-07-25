"""Provider-independent ports for temporary asset publication."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
    PublishedAssetManifest,
    PublishedAssetSourceManifest,
)


class AssetPublisher(Protocol):
    name: str

    async def publish(
        self,
        *,
        asset: PublishableAsset,
        expires_at: datetime,
    ) -> AssetPublicationReceipt: ...

    async def delete(self, *, asset: PublishedAsset) -> None: ...

    async def exists(self, *, asset: PublishedAsset) -> bool: ...

    async def get_public_url(self, *, asset: PublishedAsset) -> str: ...

    async def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]: ...

    async def close(self) -> None: ...


class SignedUrlPublisher(AssetPublisher, Protocol):
    """Marker port for future publishers whose URLs carry protected signatures."""

    async def refresh_signed_url(
        self,
        *,
        asset: PublishedAsset,
        expires_at: datetime,
    ) -> AssetPublicationReceipt: ...


class PublishedAssetManifestStore(Protocol):
    async def read(
        self, *, job_id: UUID, attempt_number: int
    ) -> PublishedAssetManifest | None: ...

    async def create(self, manifest: PublishedAssetManifest) -> None: ...

    async def checkpoint(
        self,
        *,
        previous: PublishedAssetManifest,
        current: PublishedAssetManifest,
    ) -> None: ...

    async def list_manifests(self) -> tuple[PublishedAssetManifest, ...]: ...


class PublishableAssetSource(Protocol):
    async def collect(self) -> tuple[PublishableAsset, ...]: ...


class PublishedAssetDurabilityVerifier(Protocol):
    async def source_manifest_exists(
        self,
        *,
        job_id: UUID,
        source: PublishedAssetSourceManifest,
    ) -> bool: ...

    async def binary_asset_exists(
        self,
        *,
        job_id: UUID,
        asset: PublishedAsset,
    ) -> bool: ...
