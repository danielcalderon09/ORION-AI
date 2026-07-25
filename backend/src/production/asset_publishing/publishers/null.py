"""Fail-closed publisher used when publication is disabled."""

from datetime import datetime

from backend.src.production.asset_publishing.exceptions import (
    AssetPublishingUnavailableError,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
)


class NullPublisher:
    name = "null"

    async def publish(
        self,
        *,
        asset: PublishableAsset,
        expires_at: datetime,
    ) -> AssetPublicationReceipt:
        del asset, expires_at
        raise AssetPublishingUnavailableError("asset publishing is disabled")

    async def delete(self, *, asset: PublishedAsset) -> None:
        del asset
        return None

    async def exists(self, *, asset: PublishedAsset) -> bool:
        del asset
        return False

    async def get_public_url(self, *, asset: PublishedAsset) -> str:
        del asset
        raise AssetPublishingUnavailableError("asset publishing is disabled")

    async def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]:
        del now
        return ()

    async def close(self) -> None:
        return None
