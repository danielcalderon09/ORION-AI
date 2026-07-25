"""Read-only reconciliation for durable publication records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from backend.src.production.asset_publishing.exceptions import (
    AssetPublishingError,
    PublishedAssetManifestError,
)
from backend.src.production.asset_publishing.models import (
    PublishedAsset,
    PublishedAssetStatus,
)
from backend.src.production.asset_publishing.ports import (
    AssetPublisher,
    PublishedAssetDurabilityVerifier,
    PublishedAssetManifestStore,
)
from backend.src.production.domain.base import ContractModel


class PublishedAssetReconciliationIssueCode(StrEnum):
    INVALID_MANIFEST = "invalid_manifest"
    ORPHAN_MANIFEST = "orphan_manifest"
    ORPHAN_BINARY_ASSET = "orphan_binary_asset"
    MISSING_URL = "missing_url"
    EXPIRED_URL = "expired_url"
    MISSING_PUBLICATION = "missing_publication"
    PUBLISHER_ERROR = "publisher_error"


class PublishedAssetReconciliationIssue(ContractModel):
    code: PublishedAssetReconciliationIssueCode
    job_id: UUID | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    asset_id: str | None = None
    detail: str = Field(min_length=1, max_length=300)


class PublishedAssetReconciler:
    """Reports drift without publishing, deleting, retrying, or repairing."""

    def __init__(
        self,
        *,
        manifests: PublishedAssetManifestStore,
        publisher: AssetPublisher,
        verifier: PublishedAssetDurabilityVerifier | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._manifests = manifests
        self._publisher = publisher
        self._verifier = verifier
        self._clock = clock

    async def reconcile(self) -> tuple[PublishedAssetReconciliationIssue, ...]:
        now = self._aware_now()
        try:
            manifests = await self._manifests.list_manifests()
        except PublishedAssetManifestError:
            return (
                PublishedAssetReconciliationIssue(
                    code=PublishedAssetReconciliationIssueCode.INVALID_MANIFEST,
                    detail="a contractual published-assets manifest is invalid",
                ),
            )

        issues: list[PublishedAssetReconciliationIssue] = []
        for manifest in manifests:
            if self._verifier is not None:
                for source in manifest.source_manifests:
                    if not await self._verifier.source_manifest_exists(
                        job_id=manifest.job_id,
                        source=source,
                    ):
                        issues.append(
                            PublishedAssetReconciliationIssue(
                                code=(
                                    PublishedAssetReconciliationIssueCode.ORPHAN_MANIFEST
                                ),
                                job_id=manifest.job_id,
                                attempt_number=manifest.attempt_number,
                                detail="source manifest is absent",
                            )
                        )
            for asset in manifest.entries:
                issues.extend(
                    await self._reconcile_asset(
                        job_id=manifest.job_id,
                        attempt_number=manifest.attempt_number,
                        asset=asset,
                        now=now,
                    )
                )
        return tuple(issues)

    async def _reconcile_asset(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        asset: PublishedAsset,
        now: datetime,
    ) -> tuple[PublishedAssetReconciliationIssue, ...]:
        issues: list[PublishedAssetReconciliationIssue] = []
        if self._verifier is not None and not await self._verifier.binary_asset_exists(
            job_id=job_id,
            asset=asset,
        ):
            issues.append(
                PublishedAssetReconciliationIssue(
                    code=PublishedAssetReconciliationIssueCode.ORPHAN_BINARY_ASSET,
                    detail="source binary asset is absent",
                    job_id=job_id,
                    attempt_number=attempt_number,
                    asset_id=asset.asset_id,
                )
            )
        if asset.status is not PublishedAssetStatus.PUBLISHED:
            return tuple(issues)
        if not asset.public_url or not asset.url_hash:
            issues.append(
                PublishedAssetReconciliationIssue(
                    code=PublishedAssetReconciliationIssueCode.MISSING_URL,
                    detail="published entry lacks its active URL metadata",
                    job_id=job_id,
                    attempt_number=attempt_number,
                    asset_id=asset.asset_id,
                )
            )
        if asset.expires_at is not None and asset.expires_at <= now:
            issues.append(
                PublishedAssetReconciliationIssue(
                    code=PublishedAssetReconciliationIssueCode.EXPIRED_URL,
                    detail="published URL has expired and requires cleanup",
                    job_id=job_id,
                    attempt_number=attempt_number,
                    asset_id=asset.asset_id,
                )
            )
        try:
            exists = await self._publisher.exists(asset=asset)
        except AssetPublishingError:
            issues.append(
                PublishedAssetReconciliationIssue(
                    code=PublishedAssetReconciliationIssueCode.PUBLISHER_ERROR,
                    detail="publisher could not verify the publication",
                    job_id=job_id,
                    attempt_number=attempt_number,
                    asset_id=asset.asset_id,
                )
            )
        else:
            if not exists:
                issues.append(
                    PublishedAssetReconciliationIssue(
                        code=(
                            PublishedAssetReconciliationIssueCode.MISSING_PUBLICATION
                        ),
                        detail="published bytes or sidecar are absent",
                        job_id=job_id,
                        attempt_number=attempt_number,
                        asset_id=asset.asset_id,
                    )
                )
        return tuple(issues)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("asset reconciliation clock must be timezone-aware")
        return value
