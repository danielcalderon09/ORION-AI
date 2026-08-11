"""Durable hybrid visual acquisition driven by an authorized aggregate plan."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.visual_strategy import VisualMode, VisualMotionMode
from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
)
from backend.src.production.image_acquisition.exceptions import ImageAcquisitionProviderError
from backend.src.production.image_acquisition.ports import (
    ImageAcquisitionProvider,
    ImageAcquisitionProviderRequest,
)
from backend.src.production.planning.aggregate_visual_budget import (
    AggregateVisualBudgetPlan,
    ImageRequirementKind,
)
from backend.src.production.planning.visual_strategy import HybridVisualStrategyPlan
from backend.src.production.runtime.context import StageContext
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
)


class HybridAssetOrigin(StrEnum):
    GENERATED_VIDEO_FIRST_FRAME = "generated_video_first_frame"
    GENERATED_IMAGE = "generated_image"
    REUSED_IMAGE = "reused_image"
    REUSED_VIDEO = "reused_video"


class HybridAssetStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class ReusableAssetType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class HybridAcquisitionManifestStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class HybridImageProviderAttemptStatus(StrEnum):
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class HybridImageCostSource(StrEnum):
    REPORTED = "reported"
    ESTIMATED_FALLBACK = "estimated_fallback"


class HybridImageProviderAttempt(ContractModel):
    """Sanitized durable evidence for one provider submission."""

    job_id: UUID
    stage_attempt_number: int = Field(ge=1)
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    attempt_number: int = Field(ge=1)
    purpose: ImageRequirementKind
    provider: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=300)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    reported_cost_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=9
    )
    accounted_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    cost_source: HybridImageCostSource
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, max_length=200)
    provider_retry_count: int = Field(default=0, ge=0, le=50)
    submission_started_at: datetime
    submitted_at: datetime | None = None
    terminal_at: datetime
    status: HybridImageProviderAttemptStatus
    safe_error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    artifact_relative_path: str | None = Field(default=None, max_length=1000)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_size_bytes: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_accounting(self) -> HybridImageProviderAttempt:
        expected = (
            self.reported_cost_usd
            if self.reported_cost_usd is not None
            else self.estimated_cost_usd
        )
        if self.accounted_cost_usd != expected:
            raise ValueError("image provider accounted cost differs from source")
        if self.cost_source is HybridImageCostSource.REPORTED:
            if self.reported_cost_usd is None:
                raise ValueError("reported cost source requires reported cost")
        elif self.reported_cost_usd is not None:
            raise ValueError("fallback cost source cannot contain reported cost")
        if (
            self.status is HybridImageProviderAttemptStatus.COMPLETED
            and (self.artifact_sha256 is None or self.artifact_relative_path is None)
        ):
            raise ValueError("completed image attempt requires artifact identity")
        if self.submitted_at is not None and self.submitted_at < self.submission_started_at:
            raise ValueError("image submission timestamp precedes start")
        if self.terminal_at < self.submission_started_at:
            raise ValueError("image terminal timestamp precedes start")
        return self


class HybridImageAcquisitionAccounting(ContractModel):
    image_request_count: int = Field(ge=0)
    first_frame_request_count: int = Field(ge=0)
    final_image_request_count: int = Field(ge=0)
    estimated_image_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    reported_image_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    accounted_image_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    reported_cost_request_count: int = Field(ge=0)
    estimated_fallback_request_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> HybridImageAcquisitionAccounting:
        if self.first_frame_request_count + self.final_image_request_count > self.image_request_count:
            raise ValueError("image purpose counts exceed image requests")
        if self.reported_cost_request_count + self.estimated_fallback_request_count != self.image_request_count:
            raise ValueError("image cost source counts differ from requests")
        return self


class ReusableVisualAsset(ContractModel):
    source_asset_id: str = Field(min_length=3, max_length=200)
    local_asset_id: str = Field(min_length=1, max_length=200)
    asset_type: ReusableAssetType
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    width: int | None = Field(default=None, gt=0, le=16_384)
    height: int | None = Field(default=None, gt=0, le=16_384)
    storage_reference: str = Field(min_length=1, max_length=1000)
    provenance: str = Field(min_length=1, max_length=200)
    owner_job_id: UUID | None = None

    @model_validator(mode="after")
    def validate_media_metadata(self) -> ReusableVisualAsset:
        if self.asset_type is ReusableAssetType.IMAGE and (
            self.width is None or self.height is None
        ):
            raise ValueError("reusable image requires dimensions")
        return self


class StoredGeneratedVisualAsset(ContractModel):
    local_asset_id: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    storage_reference: str = Field(min_length=1, max_length=1000)
    provenance: str = Field(min_length=1, max_length=200)


class HybridAssetAcquisitionEntry(ContractModel):
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    visual_mode: VisualMode
    motion_mode: VisualMotionMode
    usable_duration_ms: int = Field(gt=0, le=600_000)
    source_asset_id: str | None = Field(default=None, min_length=3, max_length=200)
    origin: HybridAssetOrigin
    image_requirement: ImageRequirementKind | None = None
    strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimated_cost_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=9
    )
    provider_attempts: tuple[HybridImageProviderAttempt, ...] = Field(
        default=(), max_length=50
    )
    status: HybridAssetStatus
    provider_image_generated: bool = False
    reused: bool = False
    local_asset_id: str | None = Field(default=None, min_length=1, max_length=200)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    mime_type: str | None = Field(default=None, min_length=1, max_length=100)
    width: int | None = Field(default=None, gt=0, le=16_384)
    height: int | None = Field(default=None, gt=0, le=16_384)
    storage_reference: str | None = Field(default=None, min_length=1, max_length=1000)
    provenance: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_entry(self) -> HybridAssetAcquisitionEntry:
        generated = self.visual_mode in {
            VisualMode.GENERATED_VIDEO,
            VisualMode.GENERATED_IMAGE,
        }
        if generated != (self.image_requirement is not None):
            raise ValueError("generated shots require exactly one image requirement")
        if generated != self.provider_image_generated and self.status is HybridAssetStatus.RESOLVED:
            raise ValueError("resolved generated asset must record its provider image")
        if self.reused != (self.visual_mode in {VisualMode.REUSED_IMAGE, VisualMode.REUSED_VIDEO}):
            raise ValueError("reused flag differs from visual mode")
        expected_origin = {
            VisualMode.GENERATED_VIDEO: HybridAssetOrigin.GENERATED_VIDEO_FIRST_FRAME,
            VisualMode.GENERATED_IMAGE: HybridAssetOrigin.GENERATED_IMAGE,
            VisualMode.REUSED_IMAGE: HybridAssetOrigin.REUSED_IMAGE,
            VisualMode.REUSED_VIDEO: HybridAssetOrigin.REUSED_VIDEO,
        }[self.visual_mode]
        if self.origin is not expected_origin:
            raise ValueError("asset origin differs from visual mode")
        resolved_fields = (
            self.local_asset_id,
            self.sha256,
            self.mime_type,
            self.storage_reference,
            self.provenance,
        )
        if self.status is HybridAssetStatus.RESOLVED:
            if any(value is None for value in resolved_fields):
                raise ValueError("resolved hybrid asset requires durable provenance")
            if self.visual_mode is not VisualMode.REUSED_VIDEO and (
                self.width is None or self.height is None
            ):
                raise ValueError("resolved image asset requires dimensions")
        elif any(value is not None for value in (*resolved_fields, self.width, self.height)):
            raise ValueError("pending hybrid asset cannot claim resolved provenance")
        return self


class HybridAssetAcquisitionManifest(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^1\.0\.0$")
    job_id: UUID
    source_visual_asset_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: HybridAcquisitionManifestStatus
    entries: tuple[HybridAssetAcquisitionEntry, ...] = Field(min_length=1, max_length=500)
    accounting: HybridImageAcquisitionAccounting | None = None
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def calculated_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        if self.accounting is None:
            payload.pop("accounting", None)
            for entry in payload["entries"]:
                entry.pop("estimated_cost_usd", None)
                entry.pop("provider_attempts", None)
        return _sha256_json(payload)

    @model_validator(mode="after")
    def validate_manifest(self) -> HybridAssetAcquisitionManifest:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("hybrid acquisition manifest fingerprint differs")
        if tuple(item.shot_id for item in self.entries) != tuple(
            sorted(item.shot_id for item in self.entries)
        ):
            raise ValueError("hybrid acquisition entries must be canonical")
        if len({item.shot_id for item in self.entries}) != len(self.entries):
            raise ValueError("hybrid acquisition shot identities must be unique")
        if any(
            item.strategy_fingerprint != self.strategy_fingerprint
            or item.budget_fingerprint != self.budget_fingerprint
            for item in self.entries
        ):
            raise ValueError("hybrid acquisition entry provenance differs")
        if self.accounting is not None:
            expected_accounting = summarize_hybrid_image_accounting(self.entries)
            if self.accounting != expected_accounting:
                raise ValueError("hybrid image accounting differs from entries")
        complete = all(item.status is HybridAssetStatus.RESOLVED for item in self.entries)
        if (self.status is HybridAcquisitionManifestStatus.COMPLETED) != complete:
            raise ValueError("hybrid acquisition completion differs from entries")
        return self


class HybridAssetAcquisitionSource(ContractModel):
    visual_asset_plan: ProductionVisualAssetPlan
    visual_asset_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_plan: HybridVisualStrategyPlan
    budget_plan: AggregateVisualBudgetPlan


class ReusableVisualAssetCatalog(Protocol):
    async def resolve(self, source_asset_id: str) -> ReusableVisualAsset | None: ...


class HybridGeneratedAssetStore(Protocol):
    async def store_generated(
        self,
        *,
        job_id: UUID,
        entry: HybridAssetAcquisitionEntry,
        content: bytes,
        mime_type: str,
        width: int,
        height: int,
    ) -> StoredGeneratedVisualAsset: ...


class HybridAssetAcquisitionManifestWriter(Protocol):
    async def read(self) -> HybridAssetAcquisitionManifest | None: ...
    async def create(self, manifest: HybridAssetAcquisitionManifest) -> None: ...
    async def checkpoint(
        self,
        previous: HybridAssetAcquisitionManifest,
        current: HybridAssetAcquisitionManifest,
    ) -> None: ...


class InMemoryHybridAssetAcquisitionManifestWriter:
    def __init__(self) -> None:
        self.content: bytes | None = None

    async def read(self) -> HybridAssetAcquisitionManifest | None:
        return (
            None if self.content is None else deserialize_hybrid_acquisition_manifest(self.content)
        )

    async def create(self, manifest: HybridAssetAcquisitionManifest) -> None:
        if self.content is not None:
            raise ValueError("hybrid acquisition manifest already exists")
        self.content = serialize_hybrid_acquisition_manifest(manifest)

    async def checkpoint(
        self,
        previous: HybridAssetAcquisitionManifest,
        current: HybridAssetAcquisitionManifest,
    ) -> None:
        if self.content != serialize_hybrid_acquisition_manifest(previous):
            raise ValueError("hybrid acquisition manifest changed concurrently")
        _validate_transition(previous, current)
        self.content = serialize_hybrid_acquisition_manifest(current)


class HybridAssetAcquisitionError(ValueError):
    """Fail-closed hybrid acquisition planning or recovery error."""


class HybridAssetAcquisitionCoordinator:
    """Acquire exactly the authorized generated images and pin reused assets."""

    def __init__(
        self,
        *,
        provider: ImageAcquisitionProvider,
        generated_store: HybridGeneratedAssetStore,
        reusable_catalog: ReusableVisualAssetCatalog,
        manifest_writer: HybridAssetAcquisitionManifestWriter,
        configuration: ImageAcquisitionConfiguration,
    ) -> None:
        self._provider = provider
        self._generated_store = generated_store
        self._reusable_catalog = reusable_catalog
        self._manifest_writer = manifest_writer
        self._configuration = configuration

    async def execute(
        self,
        *,
        source: HybridAssetAcquisitionSource,
        command_id: UUID,
        context: StageContext,
    ) -> HybridAssetAcquisitionManifest:
        expected = build_hybrid_acquisition_manifest(source)
        current = await self._manifest_writer.read()
        if current is None:
            current = expected
            await self._manifest_writer.create(current)
        else:
            _validate_recovery_source(current, expected)

        reusable = await self._preflight_reused_assets(current)
        assets = {item.asset_id: item for item in source.visual_asset_plan.assets}
        for expected_entry in expected.entries:
            entry = _entry_for(current, expected_entry.shot_id)
            if entry.status is HybridAssetStatus.RESOLVED:
                if entry.reused:
                    _validate_reused_resolution(entry, reusable[entry.shot_id])
                continue
            if entry.reused:
                replacement = _resolved_reused_entry(entry, reusable[entry.shot_id])
            else:
                spec = assets[entry.visual_asset_id]
                provider_attempt_number = len(entry.provider_attempts) + 1
                started = _now()
                try:
                    response = await self._provider.generate_image(
                        ImageAcquisitionProviderRequest(
                            job_id=source.strategy_plan.job_id,
                            command_id=command_id,
                            correlation_id=context.correlation_id,
                            attempt_number=context.attempt_number,
                            visual_asset=spec,
                            video_identity=source.visual_asset_plan.video_identity,
                            configuration=self._configuration,
                        )
                    )
                except ImageAcquisitionProviderError as exc:
                    failed_attempt = _provider_attempt_from_error(
                        entry=entry,
                        attempt_number=provider_attempt_number,
                        started=started,
                        error=exc,
                        job_id=source.strategy_plan.job_id,
                        stage_attempt_number=context.attempt_number,
                    )
                    failed_entry = entry.model_copy(
                        update={
                            "provider_attempts": (
                                *entry.provider_attempts,
                                failed_attempt,
                            )
                        }
                    )
                    failed_manifest = _replace_entry(current, failed_entry)
                    await self._manifest_writer.checkpoint(current, failed_manifest)
                    current = failed_manifest
                    raise
                except Exception:
                    failed_attempt = _provider_attempt(
                        entry=entry,
                        attempt_number=provider_attempt_number,
                        status=HybridImageProviderAttemptStatus.FAILED,
                        started=started,
                        terminal=_now(),
                        provider="unknown",
                        model=None,
                        safe_error_code="provider_error",
                        job_id=source.strategy_plan.job_id,
                        stage_attempt_number=context.attempt_number,
                    )
                    failed_entry = entry.model_copy(
                        update={
                            "provider_attempts": (
                                *entry.provider_attempts,
                                failed_attempt,
                            )
                        }
                    )
                    failed_manifest = _replace_entry(current, failed_entry)
                    await self._manifest_writer.checkpoint(current, failed_manifest)
                    raise
                if len(response.images) != 1:
                    raise HybridAssetAcquisitionError(
                        "hybrid image provider must return exactly one image"
                    )
                payload = response.images[0]
                mime_type = payload.mime_type or "image/png"
                width = _positive_dimension(payload.provider_metadata.get("width")) or spec.width
                height = _positive_dimension(payload.provider_metadata.get("height")) or spec.height
                stored = await self._generated_store.store_generated(
                    job_id=source.strategy_plan.job_id,
                    entry=entry,
                    content=payload.content,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                )
                completed_attempt = _provider_attempt(
                    entry=entry,
                    attempt_number=provider_attempt_number,
                    status=HybridImageProviderAttemptStatus.COMPLETED,
                    started=started,
                    terminal=_now(),
                    provider=response.provider,
                    model=response.reported_model or response.requested_model,
                    reported_cost=response.cost_usd,
                    http_status=response.http_status,
                    provider_request_id=response.request_id,
                    artifact_relative_path=stored.storage_reference,
                    artifact_sha256=stored.sha256,
                    artifact_size_bytes=len(payload.content),
                    job_id=source.strategy_plan.job_id,
                    stage_attempt_number=context.attempt_number,
                )
                replacement = entry.model_copy(
                    update={
                        "status": HybridAssetStatus.RESOLVED,
                        "provider_image_generated": True,
                        "local_asset_id": stored.local_asset_id,
                        "sha256": stored.sha256,
                        "mime_type": stored.mime_type,
                        "width": stored.width,
                        "height": stored.height,
                        "storage_reference": stored.storage_reference,
                        "provenance": stored.provenance,
                        "provider_attempts": (
                            *entry.provider_attempts,
                            completed_attempt,
                        ),
                    }
                )
            updated = _replace_entry(current, replacement)
            await self._manifest_writer.checkpoint(current, updated)
            current = updated
        if current.status is not HybridAcquisitionManifestStatus.COMPLETED:
            completed = _replace_manifest(current, status=HybridAcquisitionManifestStatus.COMPLETED)
            await self._manifest_writer.checkpoint(current, completed)
            current = completed
        return current

    async def _preflight_reused_assets(
        self,
        manifest: HybridAssetAcquisitionManifest,
    ) -> dict[str, ReusableVisualAsset]:
        resolved: dict[str, ReusableVisualAsset] = {}
        for entry in manifest.entries:
            if not entry.reused:
                continue
            if entry.source_asset_id is None:
                raise HybridAssetAcquisitionError("reused asset omitted source identity")
            asset = await self._reusable_catalog.resolve(entry.source_asset_id)
            if asset is None:
                raise HybridAssetAcquisitionError("reused source asset is unavailable")
            expected_type = (
                ReusableAssetType.IMAGE
                if entry.visual_mode is VisualMode.REUSED_IMAGE
                else ReusableAssetType.VIDEO
            )
            if asset.asset_type is not expected_type:
                raise HybridAssetAcquisitionError("reused source asset type differs")
            resolved[entry.shot_id] = asset
        return resolved


def build_hybrid_acquisition_manifest(
    source: HybridAssetAcquisitionSource,
) -> HybridAssetAcquisitionManifest:
    strategy = source.strategy_plan
    budget = source.budget_plan
    if budget.job_id != strategy.job_id:
        raise HybridAssetAcquisitionError("strategy and budget job identities differ")
    if budget.source_strategy_fingerprint != strategy.fingerprint:
        raise HybridAssetAcquisitionError("budget does not pin the supplied strategy")
    if not budget.budget_pass:
        raise HybridAssetAcquisitionError("aggregate visual budget is not authorized")
    asset_by_id = {item.asset_id: item for item in source.visual_asset_plan.assets}
    requirement_by_shot = {item.shot_id: item for item in budget.image_requirements}
    expected_generated = {
        shot.shot_id
        for shot in strategy.shots
        if shot.visual_mode in {VisualMode.GENERATED_VIDEO, VisualMode.GENERATED_IMAGE}
    }
    if set(requirement_by_shot) != expected_generated:
        raise HybridAssetAcquisitionError("budget image requirements differ from strategy")
    entries: list[HybridAssetAcquisitionEntry] = []
    for shot in strategy.shots:
        spec = asset_by_id.get(shot.visual_asset_id)
        if spec is None or spec.source_shot_id != shot.shot_id:
            raise HybridAssetAcquisitionError("strategy shot lacks matching visual intent")
        requirement = requirement_by_shot.get(shot.shot_id)
        expected_requirement = (
            ImageRequirementKind.VIDEO_FIRST_FRAME
            if shot.visual_mode is VisualMode.GENERATED_VIDEO
            else ImageRequirementKind.IMAGE_VISUAL
            if shot.visual_mode is VisualMode.GENERATED_IMAGE
            else None
        )
        if requirement is not None and requirement.requirement is not expected_requirement:
            raise HybridAssetAcquisitionError("budget image purpose differs from strategy")
        entries.append(
            HybridAssetAcquisitionEntry(
                shot_id=shot.shot_id,
                visual_asset_id=shot.visual_asset_id,
                visual_mode=shot.visual_mode,
                motion_mode=shot.motion_mode,
                usable_duration_ms=shot.usable_duration_ms,
                source_asset_id=shot.source_asset_id,
                origin={
                    VisualMode.GENERATED_VIDEO: HybridAssetOrigin.GENERATED_VIDEO_FIRST_FRAME,
                    VisualMode.GENERATED_IMAGE: HybridAssetOrigin.GENERATED_IMAGE,
                    VisualMode.REUSED_IMAGE: HybridAssetOrigin.REUSED_IMAGE,
                    VisualMode.REUSED_VIDEO: HybridAssetOrigin.REUSED_VIDEO,
                }[shot.visual_mode],
                image_requirement=expected_requirement,
                strategy_fingerprint=strategy.fingerprint,
                budget_fingerprint=budget.fingerprint,
                request_identity=_request_identity(
                    visual_plan_sha256=source.visual_asset_plan_sha256,
                    strategy_fingerprint=strategy.fingerprint,
                    budget_fingerprint=budget.fingerprint,
                    shot=shot.model_dump(mode="json"),
                    visual_asset=spec.model_dump(mode="json"),
                ),
                estimated_cost_usd=(
                    requirement.estimated_cost_usd
                    if requirement is not None
                    else None
                ),
                status=HybridAssetStatus.PENDING,
                reused=shot.visual_mode in {VisualMode.REUSED_IMAGE, VisualMode.REUSED_VIDEO},
            )
        )
    return _new_manifest(
        job_id=strategy.job_id,
        visual_plan_sha256=source.visual_asset_plan_sha256,
        strategy_fingerprint=strategy.fingerprint,
        budget_fingerprint=budget.fingerprint,
        entries=tuple(entries),
    )


def summarize_hybrid_image_accounting(
    entries: tuple[HybridAssetAcquisitionEntry, ...],
) -> HybridImageAcquisitionAccounting:
    attempts = tuple(
        attempt
        for entry in entries
        for attempt in entry.provider_attempts
    )
    return HybridImageAcquisitionAccounting(
        image_request_count=len(attempts),
        first_frame_request_count=sum(
            attempt.purpose is ImageRequirementKind.VIDEO_FIRST_FRAME for attempt in attempts
        ),
        final_image_request_count=sum(
            attempt.purpose is ImageRequirementKind.IMAGE_VISUAL for attempt in attempts
        ),
        estimated_image_cost_usd=sum(
            (attempt.estimated_cost_usd for attempt in attempts), Decimal("0")
        ),
        reported_image_cost_usd=sum(
            (
                attempt.reported_cost_usd
                for attempt in attempts
                if attempt.reported_cost_usd is not None
            ),
            Decimal("0"),
        ),
        accounted_image_cost_usd=sum(
            (attempt.accounted_cost_usd for attempt in attempts), Decimal("0")
        ),
        reported_cost_request_count=sum(
            attempt.reported_cost_usd is not None for attempt in attempts
        ),
        estimated_fallback_request_count=sum(
            attempt.reported_cost_usd is None for attempt in attempts
        ),
    )


def serialize_hybrid_acquisition_manifest(manifest: HybridAssetAcquisitionManifest) -> bytes:
    return _canonical_json(manifest.model_dump(mode="json"))


def deserialize_hybrid_acquisition_manifest(content: bytes) -> HybridAssetAcquisitionManifest:
    return HybridAssetAcquisitionManifest.model_validate(_strict_json(content))


def _now() -> datetime:
    return datetime.now(UTC)


def _provider_attempt(
    *,
    entry: HybridAssetAcquisitionEntry,
    attempt_number: int,
    status: HybridImageProviderAttemptStatus,
    started: datetime,
    terminal: datetime,
    provider: str,
    model: str | None,
    reported_cost: Decimal | None = None,
    http_status: int | None = None,
    provider_request_id: str | None = None,
    safe_error_code: str | None = None,
    artifact_relative_path: str | None = None,
    artifact_sha256: str | None = None,
    artifact_size_bytes: int | None = None,
    job_id: UUID,
    stage_attempt_number: int,
) -> HybridImageProviderAttempt:
    estimated = entry.estimated_cost_usd or Decimal("0")
    return HybridImageProviderAttempt(
        job_id=job_id,
        stage_attempt_number=stage_attempt_number,
        scene_id=entry.shot_id.split("-shot-", 1)[0],
        attempt_number=attempt_number,
        purpose=entry.image_requirement or ImageRequirementKind.IMAGE_VISUAL,
        provider=provider,
        model=model,
        estimated_cost_usd=estimated,
        reported_cost_usd=reported_cost,
        accounted_cost_usd=(reported_cost if reported_cost is not None else estimated),
        cost_source=(
            HybridImageCostSource.REPORTED
            if reported_cost is not None
            else HybridImageCostSource.ESTIMATED_FALLBACK
        ),
        http_status=http_status,
        provider_request_id=provider_request_id,
        provider_retry_count=0,
        submission_started_at=started,
        submitted_at=started,
        terminal_at=terminal,
        status=status,
        safe_error_code=safe_error_code,
        artifact_relative_path=artifact_relative_path,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=artifact_size_bytes,
    )


def _provider_attempt_from_error(
    *,
    entry: HybridAssetAcquisitionEntry,
    attempt_number: int,
    started: datetime,
    error: ImageAcquisitionProviderError,
    job_id: UUID,
    stage_attempt_number: int,
) -> HybridImageProviderAttempt:
    subtype = getattr(error, "diagnostic_subtype", None)
    code = subtype.value if subtype is not None else "provider_error"
    uncertain = error.__class__.__name__.endswith("UncertainException")
    return _provider_attempt(
        entry=entry,
        attempt_number=attempt_number,
        status=(
            HybridImageProviderAttemptStatus.UNCERTAIN
            if uncertain
            else HybridImageProviderAttemptStatus.FAILED
        ),
        started=started,
        terminal=_now(),
        provider="openrouter",
        model=getattr(error, "requested_model", None),
        reported_cost=getattr(error, "cost_usd", None),
        http_status=getattr(error, "http_status", None),
        provider_request_id=getattr(error, "provider_request_id", None),
        safe_error_code=code,
        job_id=job_id,
        stage_attempt_number=stage_attempt_number,
    )


def _new_manifest(
    *,
    job_id: UUID,
    visual_plan_sha256: str,
    strategy_fingerprint: str,
    budget_fingerprint: str,
    entries: tuple[HybridAssetAcquisitionEntry, ...],
) -> HybridAssetAcquisitionManifest:
    provisional = HybridAssetAcquisitionManifest.model_construct(
        job_id=job_id,
        source_visual_asset_plan_sha256=visual_plan_sha256,
        strategy_fingerprint=strategy_fingerprint,
        budget_fingerprint=budget_fingerprint,
        status=HybridAcquisitionManifestStatus.IN_PROGRESS,
        entries=entries,
        accounting=summarize_hybrid_image_accounting(entries),
        fingerprint="0" * 64,
    )
    return HybridAssetAcquisitionManifest(
        job_id=job_id,
        source_visual_asset_plan_sha256=visual_plan_sha256,
        strategy_fingerprint=strategy_fingerprint,
        budget_fingerprint=budget_fingerprint,
        status=HybridAcquisitionManifestStatus.IN_PROGRESS,
        entries=entries,
        accounting=summarize_hybrid_image_accounting(entries),
        fingerprint=provisional.calculated_fingerprint(),
    )


def _replace_entry(
    manifest: HybridAssetAcquisitionManifest,
    replacement: HybridAssetAcquisitionEntry,
) -> HybridAssetAcquisitionManifest:
    entries = tuple(
        replacement if item.shot_id == replacement.shot_id else item for item in manifest.entries
    )
    status = (
        HybridAcquisitionManifestStatus.COMPLETED
        if all(item.status is HybridAssetStatus.RESOLVED for item in entries)
        else HybridAcquisitionManifestStatus.IN_PROGRESS
    )
    return _replace_manifest(manifest, entries=entries, status=status)


def _replace_manifest(
    manifest: HybridAssetAcquisitionManifest,
    *,
    entries: tuple[HybridAssetAcquisitionEntry, ...] | None = None,
    status: HybridAcquisitionManifestStatus | None = None,
) -> HybridAssetAcquisitionManifest:
    resolved_entries = entries if entries is not None else manifest.entries
    resolved_status = status if status is not None else manifest.status
    provisional = HybridAssetAcquisitionManifest.model_construct(
        schema_version=manifest.schema_version,
        job_id=manifest.job_id,
        source_visual_asset_plan_sha256=manifest.source_visual_asset_plan_sha256,
        strategy_fingerprint=manifest.strategy_fingerprint,
        budget_fingerprint=manifest.budget_fingerprint,
        status=resolved_status,
        entries=resolved_entries,
        accounting=summarize_hybrid_image_accounting(resolved_entries),
        fingerprint="0" * 64,
    )
    return HybridAssetAcquisitionManifest(
        schema_version=manifest.schema_version,
        job_id=manifest.job_id,
        source_visual_asset_plan_sha256=manifest.source_visual_asset_plan_sha256,
        strategy_fingerprint=manifest.strategy_fingerprint,
        budget_fingerprint=manifest.budget_fingerprint,
        status=resolved_status,
        entries=resolved_entries,
        accounting=summarize_hybrid_image_accounting(resolved_entries),
        fingerprint=provisional.calculated_fingerprint(),
    )


def _validate_recovery_source(
    current: HybridAssetAcquisitionManifest,
    expected: HybridAssetAcquisitionManifest,
) -> None:
    current_identity = tuple(
        (item.shot_id, item.visual_asset_id, item.request_identity) for item in current.entries
    )
    expected_identity = tuple(
        (item.shot_id, item.visual_asset_id, item.request_identity) for item in expected.entries
    )
    if (
        current.job_id != expected.job_id
        or current.source_visual_asset_plan_sha256 != expected.source_visual_asset_plan_sha256
        or current.strategy_fingerprint != expected.strategy_fingerprint
        or current.budget_fingerprint != expected.budget_fingerprint
        or current_identity != expected_identity
    ):
        raise HybridAssetAcquisitionError("hybrid acquisition recovery source drifted")


def _validate_transition(
    previous: HybridAssetAcquisitionManifest,
    current: HybridAssetAcquisitionManifest,
) -> None:
    _validate_recovery_source(previous, current)
    for old, new in zip(previous.entries, current.entries, strict=True):
        if old.status is HybridAssetStatus.RESOLVED and old != new:
            raise HybridAssetAcquisitionError("resolved hybrid asset is immutable")
        if old.status is HybridAssetStatus.PENDING and new.status not in {
            HybridAssetStatus.PENDING,
            HybridAssetStatus.RESOLVED,
        }:
            raise HybridAssetAcquisitionError("invalid hybrid acquisition transition")


def _resolved_reused_entry(
    entry: HybridAssetAcquisitionEntry,
    asset: ReusableVisualAsset,
) -> HybridAssetAcquisitionEntry:
    return entry.model_copy(
        update={
            "status": HybridAssetStatus.RESOLVED,
            "local_asset_id": asset.local_asset_id,
            "sha256": asset.sha256,
            "mime_type": asset.mime_type,
            "width": asset.width,
            "height": asset.height,
            "storage_reference": asset.storage_reference,
            "provenance": asset.provenance,
        }
    )


def _validate_reused_resolution(
    entry: HybridAssetAcquisitionEntry,
    asset: ReusableVisualAsset,
) -> None:
    expected = _resolved_reused_entry(
        entry.model_copy(
            update={
                "status": HybridAssetStatus.PENDING,
                "local_asset_id": None,
                "sha256": None,
                "mime_type": None,
                "width": None,
                "height": None,
                "storage_reference": None,
                "provenance": None,
            }
        ),
        asset,
    )
    fields = (
        "local_asset_id",
        "sha256",
        "mime_type",
        "width",
        "height",
        "storage_reference",
        "provenance",
    )
    if any(getattr(entry, field) != getattr(expected, field) for field in fields):
        raise HybridAssetAcquisitionError("reused source asset integrity drifted")


def _entry_for(
    manifest: HybridAssetAcquisitionManifest,
    shot_id: str,
) -> HybridAssetAcquisitionEntry:
    return next(item for item in manifest.entries if item.shot_id == shot_id)


def _request_identity(
    *,
    visual_plan_sha256: str,
    strategy_fingerprint: str,
    budget_fingerprint: str,
    shot: object,
    visual_asset: object,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "visual_plan_sha256": visual_plan_sha256,
                "strategy_fingerprint": strategy_fingerprint,
                "budget_fingerprint": budget_fingerprint,
                "shot": shot,
                "visual_asset": visual_asset,
                "schema_version": "1.0.0",
            }
        )
    ).hexdigest()


def _positive_dimension(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_json(content: bytes) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )


__all__ = [
    "HybridAssetAcquisitionCoordinator",
    "HybridAssetAcquisitionEntry",
    "HybridAssetAcquisitionError",
    "HybridAssetAcquisitionManifest",
    "HybridAssetAcquisitionSource",
    "HybridAssetOrigin",
    "HybridAssetStatus",
    "HybridAcquisitionManifestStatus",
    "HybridImageAcquisitionAccounting",
    "HybridImageCostSource",
    "HybridImageProviderAttempt",
    "HybridImageProviderAttemptStatus",
    "HybridGeneratedAssetStore",
    "InMemoryHybridAssetAcquisitionManifestWriter",
    "ReusableAssetType",
    "ReusableVisualAsset",
    "ReusableVisualAssetCatalog",
    "StoredGeneratedVisualAsset",
    "build_hybrid_acquisition_manifest",
    "deserialize_hybrid_acquisition_manifest",
    "serialize_hybrid_acquisition_manifest",
    "summarize_hybrid_image_accounting",
]
