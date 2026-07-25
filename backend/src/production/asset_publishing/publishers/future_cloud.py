"""Explicit placeholder for a future cloud-backed publisher."""

from datetime import datetime

from backend.src.production.asset_publishing.exceptions import (
    AssetPublishingUnavailableError,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
)


class FutureCloudPublisher:
    """Architecture marker only; no cloud SDK, HTTP, credentials, or storage."""

    name = "future_cloud_unavailable"

    @staticmethod
    def _unavailable() -> AssetPublishingUnavailableError:
        return AssetPublishingUnavailableError(
            "no cloud asset publisher is implemented"
        )

    async def publish(
        self,
        *,
        asset: PublishableAsset,
        expires_at: datetime,
    ) -> AssetPublicationReceipt:
        del asset, expires_at
        raise self._unavailable()

    async def delete(self, *, asset: PublishedAsset) -> None:
        del asset
        raise self._unavailable()

    async def exists(self, *, asset: PublishedAsset) -> bool:
        del asset
        raise self._unavailable()

    async def get_public_url(self, *, asset: PublishedAsset) -> str:
        del asset
        raise self._unavailable()

    async def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]:
        del now
        raise self._unavailable()

    async def close(self) -> None:
        return None
