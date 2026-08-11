"""Offline, durable hybrid video-generation boundary.

This module is intentionally not wired into the production stage registry.  It consumes
the already-authorized hybrid strategy, aggregate budget, and acquisition manifest and
only invokes a video provider for ``GENERATED_VIDEO`` shots.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.visual_strategy import VisualMode
from backend.src.production.image_acquisition.hybrid_acquisition import (
    HybridAcquisitionManifestStatus,
    HybridAssetAcquisitionEntry,
    HybridAssetAcquisitionManifest,
    HybridAssetOrigin,
    HybridAssetStatus,
)
from backend.src.production.planning.aggregate_visual_budget import (
    AggregateVisualBudgetPlan,
    PlannedVideoRequest,
)
from backend.src.production.planning.visual_strategy import HybridVisualStrategyPlan


class HybridVideoEntryStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    RESOLVED = "resolved"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"
    UNCERTAIN = "uncertain"


class HybridVideoManifestStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class HybridResolvedAssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class HybridRemoteVideoStatus(StrEnum):
    COMPLETED = "completed"


class HybridResolvedVisualAsset(ContractModel):
    kind: HybridResolvedAssetKind
    local_asset_id: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    width: int | None = Field(default=None, gt=0, le=16_384)
    height: int | None = Field(default=None, gt=0, le=16_384)
    duration_ms: int | None = Field(default=None, gt=0, le=600_000)
    storage_reference: str = Field(min_length=1, max_length=1000)
    provenance: str = Field(min_length=1, max_length=200)
    provider_generated: bool
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=300)
    remote_generation_id: str | None = Field(default=None, min_length=1, max_length=200)
    remote_status: HybridRemoteVideoStatus | None = None
    download_identity: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reported_cost_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=9
    )

    @field_validator("reported_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("reported hybrid video cost must use Decimal text")
        return value

    @model_validator(mode="after")
    def validate_media(self) -> HybridResolvedVisualAsset:
        if self.kind is HybridResolvedAssetKind.IMAGE:
            if not self.mime_type.startswith("image/"):
                raise ValueError("resolved image asset requires image MIME")
            if self.width is None or self.height is None or self.duration_ms is not None:
                raise ValueError("resolved image asset metadata is inconsistent")
            if any(
                value is not None
                for value in (
                    self.remote_generation_id,
                    self.remote_status,
                    self.download_identity,
                )
            ):
                raise ValueError("resolved image cannot claim remote video generation")
        else:
            if self.mime_type != "video/mp4" or self.duration_ms is None:
                raise ValueError("resolved video asset requires MP4 duration")
        if not self.provider_generated and (
            any(
                value is not None
                for value in (
                    self.remote_generation_id,
                    self.remote_status,
                    self.download_identity,
                )
            )
            or self.reported_cost_usd not in {None, Decimal("0")}
        ):
            raise ValueError("reused assets cannot claim provider generation cost")
        if self.provider_generated and self.kind is HybridResolvedAssetKind.VIDEO and (
            self.provider is None
            or self.model is None
            or self.remote_generation_id is None
            or self.remote_status is not HybridRemoteVideoStatus.COMPLETED
            or self.download_identity is None
        ):
            raise ValueError("generated video lacks durable remote completion metadata")
        return self


class HybridVideoProviderRequest(ContractModel):
    job_id: UUID
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    provider_duration_seconds: int = Field(gt=0, le=600)
    usable_duration_ms: int = Field(gt=0, le=600_000)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    source_image_local_asset_id: str = Field(min_length=1, max_length=200)
    source_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_image_mime_type: str = Field(pattern=r"^image/[a-z0-9.+-]+$")
    source_image_storage_reference: str = Field(min_length=1, max_length=1000)
    video_requirement_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_identity: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("hybrid video estimate must use Decimal text")
        return value

    @model_validator(mode="after")
    def validate_request(self) -> HybridVideoProviderRequest:
        if self.provider_duration_seconds * 1_000 < self.usable_duration_ms:
            raise ValueError("hybrid video request undercovers editorial duration")
        expected = _provider_request_identity(
            job_id=self.job_id,
            shot_id=self.shot_id,
            visual_asset_id=self.visual_asset_id,
            provider_duration_seconds=self.provider_duration_seconds,
            usable_duration_ms=self.usable_duration_ms,
            estimated_cost_usd=self.estimated_cost_usd,
            source_image_local_asset_id=self.source_image_local_asset_id,
            source_image_sha256=self.source_image_sha256,
            video_requirement_identity=self.video_requirement_identity,
            strategy_fingerprint=self.strategy_fingerprint,
            budget_fingerprint=self.budget_fingerprint,
            acquisition_fingerprint=self.acquisition_fingerprint,
        )
        if self.request_identity != expected:
            raise ValueError("hybrid video provider request identity differs")
        return self


class GeneratedHybridVideoPayload(ContractModel):
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    mime_type: str = Field(pattern=r"^video/mp4$")
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    duration_ms: int = Field(gt=0, le=600_000)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    remote_generation_id: str = Field(min_length=1, max_length=200)
    remote_status: HybridRemoteVideoStatus = HybridRemoteVideoStatus.COMPLETED
    download_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    reported_cost_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=9
    )

    @field_validator("reported_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("hybrid provider cost must use Decimal text")
        return value


class StoredHybridVideoAsset(ContractModel):
    local_asset_id: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = Field(pattern=r"^video/mp4$")
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    duration_ms: int = Field(gt=0, le=600_000)
    storage_reference: str = Field(min_length=1, max_length=1000)
    provenance: str = Field(min_length=1, max_length=200)


class HybridVideoGenerationEntry(ContractModel):
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    visual_mode: VisualMode
    usable_duration_ms: int = Field(gt=0, le=600_000)
    strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition_request_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: HybridVideoEntryStatus
    provider_duration_seconds: int | None = Field(default=None, gt=0, le=600)
    estimated_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=9)
    video_requirement_identity: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    provider_request_identity: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    source_asset: HybridResolvedVisualAsset
    resolved_asset: HybridResolvedVisualAsset | None = None
    provider_call_count: int = Field(default=0, ge=0, le=20)
    poll_call_count: int = Field(default=0, ge=0, le=10_000)
    download_call_count: int = Field(default=0, ge=0, le=20)
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("hybrid video estimate must use Decimal text")
        return value

    @model_validator(mode="after")
    def validate_entry(self) -> HybridVideoGenerationEntry:
        generated = self.visual_mode is VisualMode.GENERATED_VIDEO
        video_mode = self.visual_mode in {VisualMode.GENERATED_VIDEO, VisualMode.REUSED_VIDEO}
        pinned = (
            self.provider_duration_seconds,
            self.video_requirement_identity,
            self.provider_request_identity,
        )
        if generated:
            if any(item is None for item in pinned) or self.estimated_cost_usd <= 0:
                raise ValueError("generated video entry lacks authorized purchase identity")
            if self.source_asset.kind is not HybridResolvedAssetKind.IMAGE:
                raise ValueError("generated video requires a first-frame image")
        elif any(item is not None for item in pinned) or self.estimated_cost_usd != 0:
            raise ValueError("non-generated video entry cannot claim provider purchase")
        if self.status is HybridVideoEntryStatus.RESOLVED:
            if self.resolved_asset is None:
                raise ValueError("resolved hybrid video entry requires an asset")
            expected_kind = (
                HybridResolvedAssetKind.VIDEO if video_mode else HybridResolvedAssetKind.IMAGE
            )
            if self.resolved_asset.kind is not expected_kind:
                raise ValueError("resolved hybrid asset kind differs from visual mode")
            if generated != self.resolved_asset.provider_generated:
                raise ValueError("resolved provider provenance differs from visual mode")
            if self.error_code is not None:
                raise ValueError("resolved hybrid video entry cannot contain an error")
        elif self.resolved_asset is not None:
            raise ValueError("unresolved hybrid video entry cannot claim an output asset")
        if self.status in {
            HybridVideoEntryStatus.FAILED_TRANSIENT,
            HybridVideoEntryStatus.FAILED_PERMANENT,
            HybridVideoEntryStatus.UNCERTAIN,
        } and self.error_code is None:
            raise ValueError("failed hybrid video entry requires an error code")
        return self


class HybridVideoGenerationManifest(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^1\.0\.0$")
    job_id: UUID
    strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: HybridVideoManifestStatus
    entries: tuple[HybridVideoGenerationEntry, ...] = Field(min_length=1, max_length=500)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def calculated_fingerprint(self) -> str:
        return _sha256_json(self.model_dump(mode="json", exclude={"fingerprint"}))

    @model_validator(mode="after")
    def validate_manifest(self) -> HybridVideoGenerationManifest:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("hybrid video generation manifest fingerprint differs")
        if tuple(entry.shot_id for entry in self.entries) != tuple(
            sorted(entry.shot_id for entry in self.entries)
        ):
            raise ValueError("hybrid video entries must be canonical")
        if len({entry.shot_id for entry in self.entries}) != len(self.entries):
            raise ValueError("hybrid video shot identities must be unique")
        if any(
            entry.strategy_fingerprint != self.strategy_fingerprint
            or entry.budget_fingerprint != self.budget_fingerprint
            or entry.acquisition_fingerprint != self.acquisition_fingerprint
            for entry in self.entries
        ):
            raise ValueError("hybrid video entry provenance differs")
        if self.status is HybridVideoManifestStatus.COMPLETED and any(
            entry.status is not HybridVideoEntryStatus.RESOLVED for entry in self.entries
        ):
            raise ValueError("completed hybrid video manifest requires resolved entries")
        if self.status is HybridVideoManifestStatus.UNCERTAIN and not any(
            entry.status is HybridVideoEntryStatus.UNCERTAIN for entry in self.entries
        ):
            raise ValueError("uncertain hybrid video manifest requires uncertain entry")
        return self


class HybridVideoGenerationSource(ContractModel):
    strategy_plan: HybridVisualStrategyPlan
    budget_plan: AggregateVisualBudgetPlan
    acquisition_manifest: HybridAssetAcquisitionManifest


class HybridVideoGenerationProvider(Protocol):
    async def generate_video(
        self, request: HybridVideoProviderRequest
    ) -> GeneratedHybridVideoPayload: ...


class HybridGeneratedVideoStore(Protocol):
    async def store_generated(
        self,
        *,
        job_id: UUID,
        entry: HybridVideoGenerationEntry,
        payload: GeneratedHybridVideoPayload,
    ) -> StoredHybridVideoAsset: ...


class HybridVideoGenerationManifestWriter(Protocol):
    async def read(self) -> HybridVideoGenerationManifest | None: ...
    async def create(self, manifest: HybridVideoGenerationManifest) -> None: ...
    async def checkpoint(
        self,
        previous: HybridVideoGenerationManifest,
        current: HybridVideoGenerationManifest,
    ) -> None: ...


class InMemoryHybridVideoGenerationManifestWriter:
    def __init__(self) -> None:
        self.content: bytes | None = None

    async def read(self) -> HybridVideoGenerationManifest | None:
        return None if self.content is None else deserialize_hybrid_video_manifest(self.content)

    async def create(self, manifest: HybridVideoGenerationManifest) -> None:
        if self.content is not None:
            raise ValueError("hybrid video generation manifest already exists")
        self.content = serialize_hybrid_video_manifest(manifest)

    async def checkpoint(
        self,
        previous: HybridVideoGenerationManifest,
        current: HybridVideoGenerationManifest,
    ) -> None:
        if self.content != serialize_hybrid_video_manifest(previous):
            raise ValueError("hybrid video manifest changed concurrently")
        _validate_transition(previous, current)
        self.content = serialize_hybrid_video_manifest(current)


class HybridVideoGenerationError(ValueError):
    """Fail-closed hybrid generation source, budget, or recovery error."""


class HybridVideoSubmissionUncertainError(RuntimeError):
    """Provider submission may have started and must be reconciled externally."""


class HybridVideoPermanentProviderError(RuntimeError):
    """Provider rejected a request definitively."""


class HybridVideoGenerationCoordinator:
    def __init__(
        self,
        *,
        provider: HybridVideoGenerationProvider,
        store: HybridGeneratedVideoStore,
        manifest_writer: HybridVideoGenerationManifestWriter,
    ) -> None:
        self._provider = provider
        self._store = store
        self._manifest_writer = manifest_writer

    async def execute(self, source: HybridVideoGenerationSource) -> HybridVideoGenerationManifest:
        expected = build_hybrid_video_generation_manifest(source)
        current = await self._manifest_writer.read()
        if current is None:
            current = expected
            await self._manifest_writer.create(current)
        else:
            _validate_recovery_source(current, expected)
        if any(entry.status is HybridVideoEntryStatus.UNCERTAIN for entry in current.entries):
            raise HybridVideoGenerationError("uncertain video submission requires reconciliation")

        for expected_entry in expected.entries:
            entry = _entry_for(current, expected_entry.shot_id)
            if entry.status is HybridVideoEntryStatus.RESOLVED:
                continue
            if entry.status is HybridVideoEntryStatus.FAILED_PERMANENT:
                raise HybridVideoGenerationError("permanent hybrid video failure cannot retry")
            if entry.visual_mode is not VisualMode.GENERATED_VIDEO:
                raise HybridVideoGenerationError("non-video entry was unexpectedly unresolved")
            request = _provider_request(source, entry)
            generating = entry.model_copy(
                update={"status": HybridVideoEntryStatus.GENERATING, "error_code": None}
            )
            current = await self._checkpoint_entry(current, generating)
            try:
                payload = await self._provider.generate_video(request)
            except HybridVideoSubmissionUncertainError as exc:
                uncertain = generating.model_copy(
                    update={
                        "status": HybridVideoEntryStatus.UNCERTAIN,
                        "provider_call_count": generating.provider_call_count + 1,
                        "error_code": "submission_uncertain",
                    }
                )
                await self._checkpoint_entry(current, uncertain)
                raise HybridVideoGenerationError("video submission state is uncertain") from exc
            except HybridVideoPermanentProviderError as exc:
                failed = generating.model_copy(
                    update={
                        "status": HybridVideoEntryStatus.FAILED_PERMANENT,
                        "provider_call_count": generating.provider_call_count + 1,
                        "error_code": "provider_rejected",
                    }
                )
                await self._checkpoint_entry(current, failed)
                raise HybridVideoGenerationError("video provider rejected request") from exc
            except Exception as exc:
                failed = generating.model_copy(
                    update={
                        "status": HybridVideoEntryStatus.FAILED_TRANSIENT,
                        "provider_call_count": generating.provider_call_count + 1,
                        "error_code": "provider_transient",
                    }
                )
                await self._checkpoint_entry(current, failed)
                raise HybridVideoGenerationError("transient hybrid video failure") from exc

            stored = await self._store.store_generated(
                job_id=source.strategy_plan.job_id,
                entry=generating,
                payload=payload,
            )
            resolved = generating.model_copy(
                update={
                    "status": HybridVideoEntryStatus.RESOLVED,
                    "provider_call_count": generating.provider_call_count + 1,
                    "poll_call_count": generating.poll_call_count + 1,
                    "download_call_count": generating.download_call_count + 1,
                    "resolved_asset": HybridResolvedVisualAsset(
                        kind=HybridResolvedAssetKind.VIDEO,
                        local_asset_id=stored.local_asset_id,
                        sha256=stored.sha256,
                        mime_type=stored.mime_type,
                        width=stored.width,
                        height=stored.height,
                        duration_ms=stored.duration_ms,
                        storage_reference=stored.storage_reference,
                        provenance=stored.provenance,
                        provider_generated=True,
                        provider=payload.provider,
                        model=payload.model,
                        remote_generation_id=payload.remote_generation_id,
                        remote_status=payload.remote_status,
                        download_identity=payload.download_identity,
                        reported_cost_usd=payload.reported_cost_usd,
                    ),
                    "error_code": None,
                }
            )
            current = await self._checkpoint_entry(current, resolved)
        if current.status is not HybridVideoManifestStatus.COMPLETED:
            completed = _replace_manifest(current, status=HybridVideoManifestStatus.COMPLETED)
            await self._manifest_writer.checkpoint(current, completed)
            current = completed
        return current

    async def _checkpoint_entry(
        self,
        manifest: HybridVideoGenerationManifest,
        entry: HybridVideoGenerationEntry,
    ) -> HybridVideoGenerationManifest:
        current = _replace_entry(manifest, entry)
        await self._manifest_writer.checkpoint(manifest, current)
        return current


def build_hybrid_video_generation_manifest(
    source: HybridVideoGenerationSource,
) -> HybridVideoGenerationManifest:
    strategy = source.strategy_plan
    budget = source.budget_plan
    acquisition = source.acquisition_manifest
    _validate_source(strategy, budget, acquisition)
    requirement_by_shot = {item.shot_id: item for item in budget.video_requirements}
    acquisition_by_shot = {item.shot_id: item for item in acquisition.entries}
    entries: list[HybridVideoGenerationEntry] = []
    for shot in strategy.shots:
        acquired = acquisition_by_shot[shot.shot_id]
        requirement = requirement_by_shot.get(shot.shot_id)
        source_asset = _source_asset(acquired)
        if shot.visual_mode is VisualMode.GENERATED_VIDEO:
            if requirement is None:
                raise HybridVideoGenerationError("generated video lacks purchase requirement")
            requirement_identity = _video_requirement_identity(budget.fingerprint, requirement)
            request_identity = _provider_request_identity(
                job_id=strategy.job_id,
                shot_id=shot.shot_id,
                visual_asset_id=shot.visual_asset_id,
                provider_duration_seconds=requirement.provider_duration_seconds,
                usable_duration_ms=requirement.usable_duration_ms,
                estimated_cost_usd=requirement.estimated_cost_usd,
                source_image_local_asset_id=source_asset.local_asset_id,
                source_image_sha256=source_asset.sha256,
                video_requirement_identity=requirement_identity,
                strategy_fingerprint=strategy.fingerprint,
                budget_fingerprint=budget.fingerprint,
                acquisition_fingerprint=acquisition.fingerprint,
            )
            entries.append(
                HybridVideoGenerationEntry(
                    shot_id=shot.shot_id,
                    visual_asset_id=shot.visual_asset_id,
                    visual_mode=shot.visual_mode,
                    usable_duration_ms=shot.usable_duration_ms,
                    strategy_fingerprint=strategy.fingerprint,
                    budget_fingerprint=budget.fingerprint,
                    acquisition_fingerprint=acquisition.fingerprint,
                    acquisition_request_identity=acquired.request_identity,
                    status=HybridVideoEntryStatus.PENDING,
                    provider_duration_seconds=requirement.provider_duration_seconds,
                    estimated_cost_usd=requirement.estimated_cost_usd,
                    video_requirement_identity=requirement_identity,
                    provider_request_identity=request_identity,
                    source_asset=source_asset,
                )
            )
        else:
            if requirement is not None:
                raise HybridVideoGenerationError("non-generated shot has video requirement")
            entries.append(
                HybridVideoGenerationEntry(
                    shot_id=shot.shot_id,
                    visual_asset_id=shot.visual_asset_id,
                    visual_mode=shot.visual_mode,
                    usable_duration_ms=shot.usable_duration_ms,
                    strategy_fingerprint=strategy.fingerprint,
                    budget_fingerprint=budget.fingerprint,
                    acquisition_fingerprint=acquisition.fingerprint,
                    acquisition_request_identity=acquired.request_identity,
                    status=HybridVideoEntryStatus.RESOLVED,
                    source_asset=source_asset,
                    resolved_asset=source_asset,
                )
            )
    return _new_manifest(
        job_id=strategy.job_id,
        strategy_fingerprint=strategy.fingerprint,
        budget_fingerprint=budget.fingerprint,
        acquisition_fingerprint=acquisition.fingerprint,
        entries=tuple(entries),
    )


def serialize_hybrid_video_manifest(manifest: HybridVideoGenerationManifest) -> bytes:
    return _canonical_json(manifest.model_dump(mode="json"))


def deserialize_hybrid_video_manifest(content: bytes) -> HybridVideoGenerationManifest:
    return HybridVideoGenerationManifest.model_validate(_strict_json_object(content))


def _validate_source(
    strategy: HybridVisualStrategyPlan,
    budget: AggregateVisualBudgetPlan,
    acquisition: HybridAssetAcquisitionManifest,
) -> None:
    if budget.job_id != strategy.job_id or acquisition.job_id != strategy.job_id:
        raise HybridVideoGenerationError("hybrid video job identities differ")
    if budget.source_strategy_fingerprint != strategy.fingerprint:
        raise HybridVideoGenerationError("budget does not pin hybrid strategy")
    if acquisition.strategy_fingerprint != strategy.fingerprint:
        raise HybridVideoGenerationError("acquisition does not pin hybrid strategy")
    if acquisition.budget_fingerprint != budget.fingerprint:
        raise HybridVideoGenerationError("acquisition does not pin visual budget")
    if acquisition.status is not HybridAcquisitionManifestStatus.COMPLETED:
        raise HybridVideoGenerationError("hybrid acquisition is incomplete")
    if not budget.budget_pass:
        raise HybridVideoGenerationError("aggregate visual budget is not authorized")
    if (
        budget.video_requests > budget.maximum_video_requests
        or budget.estimated_video_cost_usd > budget.maximum_authorized_video_cost_usd
        or budget.estimated_total_visual_cost_usd
        > budget.maximum_authorized_total_visual_cost_usd
        or any(
            item.estimated_cost_usd
            > budget.maximum_authorized_video_cost_per_request_usd
            for item in budget.video_requirements
        )
    ):
        raise HybridVideoGenerationError("aggregate video authorization recheck failed")
    strategy_ids = tuple(shot.shot_id for shot in strategy.shots)
    acquisition_ids = tuple(entry.shot_id for entry in acquisition.entries)
    if strategy_ids != acquisition_ids:
        raise HybridVideoGenerationError("hybrid acquisition shots differ from strategy")
    generated_ids = {
        shot.shot_id for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_VIDEO
    }
    if generated_ids != {item.shot_id for item in budget.video_requirements}:
        raise HybridVideoGenerationError("video requirements differ from generated shots")
    for shot, acquired in zip(strategy.shots, acquisition.entries, strict=True):
        if (
            acquired.status is not HybridAssetStatus.RESOLVED
            or acquired.visual_asset_id != shot.visual_asset_id
            or acquired.visual_mode is not shot.visual_mode
            or acquired.usable_duration_ms != shot.usable_duration_ms
        ):
            raise HybridVideoGenerationError("hybrid acquisition entry differs from strategy")
        if shot.visual_mode is VisualMode.GENERATED_VIDEO and (
            acquired.origin is not HybridAssetOrigin.GENERATED_VIDEO_FIRST_FRAME
            or not acquired.provider_image_generated
            or acquired.reused
            or acquired.mime_type is None
            or not acquired.mime_type.startswith("image/")
        ):
            raise HybridVideoGenerationError("generated video lacks valid first frame")
        if shot.visual_mode is VisualMode.REUSED_VIDEO and (
            acquired.origin is not HybridAssetOrigin.REUSED_VIDEO
            or acquired.provider_image_generated
            or not acquired.reused
            or acquired.mime_type != "video/mp4"
        ):
            raise HybridVideoGenerationError("reused video provenance is invalid")


def _source_asset(entry: HybridAssetAcquisitionEntry) -> HybridResolvedVisualAsset:
    required = (
        entry.local_asset_id,
        entry.sha256,
        entry.mime_type,
        entry.storage_reference,
        entry.provenance,
    )
    if any(value is None for value in required):
        raise HybridVideoGenerationError("resolved acquisition omitted durable asset data")
    video = entry.visual_mode is VisualMode.REUSED_VIDEO
    return HybridResolvedVisualAsset(
        kind=HybridResolvedAssetKind.VIDEO if video else HybridResolvedAssetKind.IMAGE,
        local_asset_id=str(entry.local_asset_id),
        sha256=str(entry.sha256),
        mime_type=str(entry.mime_type),
        width=entry.width,
        height=entry.height,
        duration_ms=entry.usable_duration_ms if video else None,
        storage_reference=str(entry.storage_reference),
        provenance=str(entry.provenance),
        provider_generated=False,
    )


def _provider_request(
    source: HybridVideoGenerationSource,
    entry: HybridVideoGenerationEntry,
) -> HybridVideoProviderRequest:
    if (
        entry.provider_duration_seconds is None
        or entry.video_requirement_identity is None
        or entry.provider_request_identity is None
    ):
        raise HybridVideoGenerationError("generated video entry is not pinned")
    return HybridVideoProviderRequest(
        job_id=source.strategy_plan.job_id,
        shot_id=entry.shot_id,
        visual_asset_id=entry.visual_asset_id,
        provider_duration_seconds=entry.provider_duration_seconds,
        usable_duration_ms=entry.usable_duration_ms,
        estimated_cost_usd=entry.estimated_cost_usd,
        source_image_local_asset_id=entry.source_asset.local_asset_id,
        source_image_sha256=entry.source_asset.sha256,
        source_image_mime_type=entry.source_asset.mime_type,
        source_image_storage_reference=entry.source_asset.storage_reference,
        video_requirement_identity=entry.video_requirement_identity,
        strategy_fingerprint=entry.strategy_fingerprint,
        budget_fingerprint=entry.budget_fingerprint,
        acquisition_fingerprint=entry.acquisition_fingerprint,
        request_identity=entry.provider_request_identity,
    )


def _new_manifest(
    *,
    job_id: UUID,
    strategy_fingerprint: str,
    budget_fingerprint: str,
    acquisition_fingerprint: str,
    entries: tuple[HybridVideoGenerationEntry, ...],
) -> HybridVideoGenerationManifest:
    status = _manifest_status(entries)
    provisional = HybridVideoGenerationManifest.model_construct(
        job_id=job_id,
        strategy_fingerprint=strategy_fingerprint,
        budget_fingerprint=budget_fingerprint,
        acquisition_fingerprint=acquisition_fingerprint,
        status=status,
        entries=entries,
        fingerprint="0" * 64,
    )
    return HybridVideoGenerationManifest(
        job_id=job_id,
        strategy_fingerprint=strategy_fingerprint,
        budget_fingerprint=budget_fingerprint,
        acquisition_fingerprint=acquisition_fingerprint,
        status=status,
        entries=entries,
        fingerprint=provisional.calculated_fingerprint(),
    )


def _replace_entry(
    manifest: HybridVideoGenerationManifest,
    replacement: HybridVideoGenerationEntry,
) -> HybridVideoGenerationManifest:
    entries = tuple(
        replacement if entry.shot_id == replacement.shot_id else entry
        for entry in manifest.entries
    )
    return _replace_manifest(manifest, entries=entries)


def _replace_manifest(
    manifest: HybridVideoGenerationManifest,
    *,
    entries: tuple[HybridVideoGenerationEntry, ...] | None = None,
    status: HybridVideoManifestStatus | None = None,
) -> HybridVideoGenerationManifest:
    values = entries or manifest.entries
    resolved_status = status or _manifest_status(values)
    provisional = manifest.model_copy(
        update={"entries": values, "status": resolved_status, "fingerprint": "0" * 64}
    )
    return manifest.model_copy(
        update={
            "entries": values,
            "status": resolved_status,
            "fingerprint": provisional.calculated_fingerprint(),
        }
    )


def _manifest_status(
    entries: tuple[HybridVideoGenerationEntry, ...],
) -> HybridVideoManifestStatus:
    if all(entry.status is HybridVideoEntryStatus.RESOLVED for entry in entries):
        return HybridVideoManifestStatus.COMPLETED
    if any(entry.status is HybridVideoEntryStatus.UNCERTAIN for entry in entries):
        return HybridVideoManifestStatus.UNCERTAIN
    if any(
        entry.status
        in {HybridVideoEntryStatus.FAILED_TRANSIENT, HybridVideoEntryStatus.FAILED_PERMANENT}
        for entry in entries
    ):
        return HybridVideoManifestStatus.FAILED
    return HybridVideoManifestStatus.IN_PROGRESS


def _validate_recovery_source(
    current: HybridVideoGenerationManifest,
    expected: HybridVideoGenerationManifest,
) -> None:
    if (
        current.job_id != expected.job_id
        or current.strategy_fingerprint != expected.strategy_fingerprint
        or current.budget_fingerprint != expected.budget_fingerprint
        or current.acquisition_fingerprint != expected.acquisition_fingerprint
        or tuple(entry.shot_id for entry in current.entries)
        != tuple(entry.shot_id for entry in expected.entries)
    ):
        raise HybridVideoGenerationError("hybrid video recovery source drifted")
    for before, planned in zip(current.entries, expected.entries, strict=True):
        immutable_before = before.model_dump(
            mode="json",
            exclude={
                "status",
                "resolved_asset",
                "provider_call_count",
                "poll_call_count",
                "download_call_count",
                "error_code",
            },
        )
        immutable_planned = planned.model_dump(
            mode="json",
            exclude={
                "status",
                "resolved_asset",
                "provider_call_count",
                "poll_call_count",
                "download_call_count",
                "error_code",
            },
        )
        if immutable_before != immutable_planned:
            raise HybridVideoGenerationError("hybrid video recovery entry drifted")


def _validate_transition(
    previous: HybridVideoGenerationManifest,
    current: HybridVideoGenerationManifest,
) -> None:
    _validate_recovery_source(previous, current)
    for before, after in zip(previous.entries, current.entries, strict=True):
        if before.status is HybridVideoEntryStatus.RESOLVED and before != after:
            raise ValueError("resolved hybrid video entry is immutable")
        if before.status is HybridVideoEntryStatus.UNCERTAIN and before != after:
            raise ValueError("uncertain hybrid video entry requires reconciliation")


def _entry_for(
    manifest: HybridVideoGenerationManifest, shot_id: str
) -> HybridVideoGenerationEntry:
    return next(entry for entry in manifest.entries if entry.shot_id == shot_id)


def _video_requirement_identity(
    budget_fingerprint: str, requirement: PlannedVideoRequest
) -> str:
    return _sha256_json(
        {
            "budget_fingerprint": budget_fingerprint,
            "requirement": requirement.model_dump(mode="json"),
        }
    )


def _provider_request_identity(
    *,
    job_id: UUID,
    shot_id: str,
    visual_asset_id: str,
    provider_duration_seconds: int,
    usable_duration_ms: int,
    estimated_cost_usd: Decimal,
    source_image_local_asset_id: str,
    source_image_sha256: str,
    video_requirement_identity: str,
    strategy_fingerprint: str,
    budget_fingerprint: str,
    acquisition_fingerprint: str,
) -> str:
    return _sha256_json(
        {
            "schema_version": "1.0.0",
            "job_id": str(job_id),
            "shot_id": shot_id,
            "visual_asset_id": visual_asset_id,
            "provider_duration_seconds": provider_duration_seconds,
            "usable_duration_ms": usable_duration_ms,
            "estimated_cost_usd": str(estimated_cost_usd),
            "source_image_local_asset_id": source_image_local_asset_id,
            "source_image_sha256": source_image_sha256,
            "video_requirement_identity": video_requirement_identity,
            "strategy_fingerprint": strategy_fingerprint,
            "budget_fingerprint": budget_fingerprint,
            "acquisition_fingerprint": acquisition_fingerprint,
        }
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json_object(content: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(value, dict):
        raise ValueError("hybrid video manifest must be a JSON object")
    return value


__all__ = [
    "GeneratedHybridVideoPayload",
    "HybridGeneratedVideoStore",
    "HybridResolvedAssetKind",
    "HybridResolvedVisualAsset",
    "HybridRemoteVideoStatus",
    "HybridVideoEntryStatus",
    "HybridVideoGenerationCoordinator",
    "HybridVideoGenerationEntry",
    "HybridVideoGenerationError",
    "HybridVideoGenerationManifest",
    "HybridVideoGenerationManifestWriter",
    "HybridVideoGenerationProvider",
    "HybridVideoGenerationSource",
    "HybridVideoManifestStatus",
    "HybridVideoPermanentProviderError",
    "HybridVideoProviderRequest",
    "HybridVideoSubmissionUncertainError",
    "InMemoryHybridVideoGenerationManifestWriter",
    "StoredHybridVideoAsset",
    "build_hybrid_video_generation_manifest",
    "deserialize_hybrid_video_manifest",
    "serialize_hybrid_video_manifest",
]
