"""Offline hybrid acquisition accounting, reuse, drift, and recovery tests."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from backend.src.production.domain.duration_resolution import DurationResolutionPolicy
from backend.src.production.domain.visual_strategy import VisualMode, VisualMotionMode
from backend.src.production.image_acquisition.configuration import ImageAcquisitionConfiguration
from backend.src.production.image_acquisition.hybrid_acquisition import (
    HybridAssetAcquisitionCoordinator,
    HybridAssetAcquisitionError,
    HybridAssetAcquisitionSource,
    HybridAssetOrigin,
    InMemoryHybridAssetAcquisitionManifestWriter,
    ReusableAssetType,
    ReusableVisualAsset,
    StoredGeneratedVisualAsset,
    build_hybrid_acquisition_manifest,
    deserialize_hybrid_acquisition_manifest,
    serialize_hybrid_acquisition_manifest,
)
from backend.src.production.image_acquisition.ports import (
    GeneratedImagePayload,
    ImageAcquisitionProviderResponse,
)
from backend.src.production.image_acquisition.providers import (
    SimulatedImageAcquisitionProvider,
)
from backend.src.production.planning import (
    HybridVisualBudgetAuthorization,
    HybridVisualStrategyPlan,
    HybridVisualStrategyPolicy,
    HybridVisualStrategySummary,
    VisualStrategyName,
    allocate_editorial_duration_plan,
    allocate_visual_shots,
    build_aggregate_visual_budget_plan,
    build_hybrid_visual_strategy_plan,
    resolve_editorial_audio_first,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.models import ProductionCamera
from backend.src.production.scripting.models import adaptive_narrative_roles
from backend.src.production.visual_asset_planning.models import (
    AssetKind,
    ContinuityEntityKind,
    GenerationMode,
    ProductionVisualAssetPlan,
    ProductionVisualAssetSpec,
    SeedPolicy,
    VisualAssetRole,
    VisualComposition,
    VisualConsistencyProfile,
    VisualContinuityEntity,
)

JOB_ID = UUID("10000000-0000-4000-8000-000000001301")
EXPANSION_ID = UUID("20000000-0000-4000-8000-000000001301")


class CountingProvider(SimulatedImageAcquisitionProvider):
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on_call = fail_on_call

    async def generate_image(self, request):
        self.calls.append(request.visual_asset.asset_id)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated transient image failure")
        return await super().generate_image(request)


class CostedProvider(CountingProvider):
    def __init__(self, reported_costs: tuple[str | None, ...]) -> None:
        super().__init__()
        self.reported_costs = reported_costs

    async def generate_image(self, request):
        response = await super().generate_image(request)
        index = len(self.calls) - 1
        return response.model_copy(
            update={
                "cost_usd": (
                    Decimal(self.reported_costs[index])
                    if self.reported_costs[index] is not None
                    else None
                ),
                "http_status": 200,
                "request_id": f"image-request-{index + 1}",
            }
        )


class MemoryGeneratedStore:
    def __init__(self) -> None:
        self.assets: dict[str, StoredGeneratedVisualAsset] = {}

    async def store_generated(
        self,
        *,
        job_id,
        entry,
        content,
        mime_type,
        width,
        height,
    ):
        stored = StoredGeneratedVisualAsset(
            local_asset_id=f"generated-{entry.visual_asset_id}",
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=mime_type,
            width=width,
            height=height,
            storage_reference=(
                f"production/{job_id}/assets/images/generated-{entry.visual_asset_id}.png"
            ),
            provenance="orion-simulated",
        )
        self.assets[entry.shot_id] = stored
        return stored


class MemoryCatalog:
    def __init__(self, assets: tuple[ReusableVisualAsset, ...] = ()) -> None:
        self.assets = {item.source_asset_id: item for item in assets}
        self.calls: list[str] = []

    async def resolve(self, source_asset_id: str):
        self.calls.append(source_asset_id)
        return self.assets.get(source_asset_id)


def _shots():
    narration = (7_000, 8_500, 9_000, 10_000, 11_000)
    editorial = allocate_editorial_duration_plan(
        requested_duration_ms=45_000,
        scene_count=5,
        narrative_roles=adaptive_narrative_roles(5),
    )
    resolved = resolve_editorial_audio_first(
        editorial,
        narration,
        DurationResolutionPolicy(),
    )
    return tuple(
        shot
        for scene in resolved.scenes
        for shot in allocate_visual_shots(scene, supported_durations_seconds=(4, 6, 8))
    )


def _strategy(name: VisualStrategyName) -> HybridVisualStrategyPlan:
    return build_hybrid_visual_strategy_plan(
        job_id=JOB_ID,
        source_shot_expansion_artifact_id=EXPANSION_ID,
        source_shot_expansion_sha256="a" * 64,
        source_shot_expansion_fingerprint="b" * 64,
        shots=_shots(),
        strategy_name=name,
        policy=HybridVisualStrategyPolicy(),
    )


def _with_two_reused_images(
    strategy: HybridVisualStrategyPlan,
) -> HybridVisualStrategyPlan:
    candidates = [
        item for item in strategy.shots if item.visual_mode is VisualMode.GENERATED_IMAGE
    ][:2]
    selected = {item.shot_id for item in candidates}
    shots = tuple(
        item.model_copy(
            update={
                "visual_mode": VisualMode.REUSED_IMAGE,
                "source_asset_id": f"reuse:{item.shot_id}",
            }
        )
        if item.shot_id in selected
        else item
        for item in strategy.shots
    )
    summary = HybridVisualStrategySummary(
        visual_shot_count=strategy.summary.visual_shot_count,
        generated_video_shots=strategy.summary.generated_video_shots,
        generated_image_shots=strategy.summary.generated_image_shots - 2,
        reused_video_shots=0,
        reused_image_shots=2,
        quality_floor_pass=strategy.summary.quality_floor_pass,
        quality_degradation_authorized=strategy.summary.quality_degradation_authorized,
        maximum_consecutive_image_shots=strategy.summary.maximum_consecutive_image_shots,
    )
    provisional = HybridVisualStrategyPlan.model_construct(
        job_id=strategy.job_id,
        source_shot_expansion_artifact_id=strategy.source_shot_expansion_artifact_id,
        source_shot_expansion_sha256=strategy.source_shot_expansion_sha256,
        source_shot_expansion_fingerprint=strategy.source_shot_expansion_fingerprint,
        strategy_name=strategy.strategy_name,
        shots=shots,
        summary=summary,
        fingerprint="0" * 64,
    )
    return HybridVisualStrategyPlan(
        job_id=strategy.job_id,
        source_shot_expansion_artifact_id=strategy.source_shot_expansion_artifact_id,
        source_shot_expansion_sha256=strategy.source_shot_expansion_sha256,
        source_shot_expansion_fingerprint=strategy.source_shot_expansion_fingerprint,
        strategy_name=strategy.strategy_name,
        shots=shots,
        summary=summary,
        fingerprint=provisional.calculated_fingerprint(),
    )


def _authorization(*, image_requests: int = 20, image_cost: str = "1.00"):
    return HybridVisualBudgetAuthorization(
        estimated_image_cost_per_request_usd=Decimal("0.04"),
        video_price_per_second_usd=Decimal("0.03"),
        maximum_image_requests=image_requests,
        maximum_video_requests=20,
        maximum_authorized_image_cost_usd=Decimal(image_cost),
        maximum_authorized_video_cost_per_request_usd=Decimal("0.25"),
        maximum_authorized_video_cost_usd=Decimal("3.00"),
        maximum_authorized_total_visual_cost_usd=Decimal("4.00"),
    )


def _visual_plan(strategy: HybridVisualStrategyPlan) -> ProductionVisualAssetPlan:
    assets = tuple(_asset(item) for item in strategy.shots)
    return ProductionVisualAssetPlan(
        source_scene_plan_schema_version="1.0.0",
        source_scene_plan_artifact_id=uuid4(),
        source_scene_plan_sha256="c" * 64,
        title="Hybrid acquisition fixture",
        language="es",
        aspect_ratio="1:1",
        global_visual_direction="Stable hybrid test direction",
        consistency_profile=VisualConsistencyProfile(
            entities=(
                VisualContinuityEntity(
                    entity_id="location_01",
                    kind=ContinuityEntityKind.LOCATION,
                    description="Stable test location",
                ),
            ),
            palette=("blue", "amber"),
            lighting_direction="Stable light",
            style_direction="Cinematic geometry",
            period="Contemporary",
            visual_identity="Hybrid test identity",
            continuity_rules=("Keep visual continuity",),
        ),
        assets=assets,
    )


def _asset(shot) -> ProductionVisualAssetSpec:
    scene_number = int(shot.scene_id.removeprefix("scene-"))
    shot_number = shot.shot_sequence_index + 1
    return ProductionVisualAssetSpec(
        asset_id=shot.visual_asset_id,
        scene_number=scene_number,
        source_scene_id=shot.scene_id,
        shot_number=shot_number,
        source_shot_id=shot.shot_id,
        role=VisualAssetRole.PRIMARY,
        asset_kind=AssetKind.STILL_IMAGE,
        generation_mode=GenerationMode.TEXT_TO_IMAGE,
        prompt=f"Distinct approved visual intent {shot.intent_key}",
        negative_prompt="No embedded text",
        visual_subject=f"Subject {shot.shot_id}",
        environment="Stable simulated environment",
        composition=VisualComposition(
            layout="Centered composition",
            focal_point="Primary subject",
            depth="Layered depth",
            action=f"Action {shot.intent_key}",
        ),
        camera_intent=ProductionCamera(
            framing="wide",
            angle="eye_level",
            movement="static",
            lens_millimeters=35,
            subject=f"Subject {shot.shot_id}",
        ),
        lighting="Soft dramatic light",
        color_direction="Blue and amber",
        style_direction="Cinematic geometry",
        continuity_group="location_01",
        width=64,
        height=64,
        aspect_ratio="1:1",
        expected_duration_seconds=shot.usable_duration_ms / 1000,
        seed_policy=SeedPolicy.DETERMINISTIC,
    )


def _source(strategy: HybridVisualStrategyPlan, *, authorization=None):
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=authorization or _authorization(),
    )
    return HybridAssetAcquisitionSource(
        visual_asset_plan=_visual_plan(strategy),
        visual_asset_plan_sha256="d" * 64,
        strategy_plan=strategy,
        budget_plan=budget,
    )


def _reusable_assets(strategy: HybridVisualStrategyPlan):
    return tuple(
        ReusableVisualAsset(
            source_asset_id=shot.source_asset_id,
            local_asset_id=f"catalog-{shot.visual_asset_id}",
            asset_type=(
                ReusableAssetType.IMAGE
                if shot.visual_mode is VisualMode.REUSED_IMAGE
                else ReusableAssetType.VIDEO
            ),
            sha256=hashlib.sha256(shot.shot_id.encode()).hexdigest(),
            mime_type=("image/png" if shot.visual_mode is VisualMode.REUSED_IMAGE else "video/mp4"),
            width=64 if shot.visual_mode is VisualMode.REUSED_IMAGE else None,
            height=64 if shot.visual_mode is VisualMode.REUSED_IMAGE else None,
            storage_reference=f"catalog/{shot.visual_asset_id}",
            provenance="workspace-owned-test-catalog",
            owner_job_id=JOB_ID,
        )
        for shot in strategy.shots
        if shot.source_asset_id is not None
    )


def _context() -> StageContext:
    return StageContext(
        job_id=JOB_ID,
        command_id=uuid4(),
        stage="acquiring_assets",
        attempt_number=1,
        input_artifact_ids=(),
        workspace_relative_path=f"production/{JOB_ID}/acquiring_assets/attempt-1",
        correlation_id=JOB_ID,
    )


async def _run(source, *, provider=None, writer=None, catalog=None):
    provider = provider or CountingProvider()
    writer = writer or InMemoryHybridAssetAcquisitionManifestWriter()
    catalog = catalog or MemoryCatalog(_reusable_assets(source.strategy_plan))
    coordinator = HybridAssetAcquisitionCoordinator(
        provider=provider,
        generated_store=MemoryGeneratedStore(),
        reusable_catalog=catalog,
        manifest_writer=writer,
        configuration=ImageAcquisitionConfiguration(),
    )
    result = await coordinator.execute(
        source=source,
        command_id=uuid4(),
        context=_context(),
    )
    return result, provider, writer, catalog


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "expected_calls", "first_frames", "final_images"),
    (
        (VisualStrategyName.HYBRID_BALANCED, 10, 5, 5),
        (VisualStrategyName.HYBRID_ECONOMY, 10, 3, 7),
        (VisualStrategyName.FULL_VIDEO, 10, 10, 0),
    ),
)
async def test_generated_modes_match_exact_authorized_image_requirements(
    name,
    expected_calls,
    first_frames,
    final_images,
) -> None:
    source = _source(_strategy(name))
    manifest, provider, _, _ = await _run(source)

    assert len(provider.calls) == expected_calls == source.budget_plan.image_requests
    assert (
        sum(
            item.origin is HybridAssetOrigin.GENERATED_VIDEO_FIRST_FRAME
            for item in manifest.entries
        )
        == first_frames
    )
    assert (
        sum(item.origin is HybridAssetOrigin.GENERATED_IMAGE for item in manifest.entries)
        == final_images
    )
    assert all(item.provider_image_generated for item in manifest.entries)


@pytest.mark.asyncio
async def test_balanced_two_reused_images_make_exactly_eight_provider_calls() -> None:
    strategy = _with_two_reused_images(_strategy(VisualStrategyName.HYBRID_BALANCED))
    source = _source(strategy)
    manifest, provider, _, _ = await _run(source)

    assert source.budget_plan.image_requests == 8
    assert len(provider.calls) == 8
    assert sum(item.reused for item in manifest.entries) == 2
    assert all(not item.provider_image_generated for item in manifest.entries if item.reused)


@pytest.mark.asyncio
async def test_rejected_budget_and_fingerprint_mismatch_make_zero_calls() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    rejected = _source(strategy, authorization=_authorization(image_requests=0, image_cost="0"))
    provider = CountingProvider()
    with pytest.raises(HybridAssetAcquisitionError, match="not authorized"):
        await _run(rejected, provider=provider)
    assert provider.calls == []

    other_strategy = _strategy(VisualStrategyName.HYBRID_ECONOMY)
    mismatched = HybridAssetAcquisitionSource(
        visual_asset_plan=_visual_plan(strategy),
        visual_asset_plan_sha256="d" * 64,
        strategy_plan=strategy,
        budget_plan=_source(other_strategy).budget_plan,
    )
    with pytest.raises(HybridAssetAcquisitionError, match="does not pin"):
        await _run(mismatched, provider=provider)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_partial_recovery_reuses_completed_images() -> None:
    source = _source(_strategy(VisualStrategyName.HYBRID_BALANCED))
    writer = InMemoryHybridAssetAcquisitionManifestWriter()
    provider = CountingProvider(fail_on_call=5)
    with pytest.raises(RuntimeError, match="transient"):
        await _run(source, provider=provider, writer=writer)
    assert len(provider.calls) == 5

    provider.fail_on_call = None
    await _run(source, provider=provider, writer=writer)
    assert len(provider.calls) == 11
    assert provider.calls[:4] != provider.calls[5:9]


@pytest.mark.asyncio
async def test_provider_telemetry_and_mixed_decimal_accounting_are_durable() -> None:
    source = _source(_strategy(VisualStrategyName.HYBRID_BALANCED))
    provider = CostedProvider(("0.031", None, "0.029", "0.031", None, "0.029", "0.031", None, "0.029", "0.031"))
    manifest, provider, _, _ = await _run(source, provider=provider)

    assert manifest.accounting is not None
    assert manifest.accounting.image_request_count == 10
    assert manifest.accounting.first_frame_request_count == 5
    assert manifest.accounting.final_image_request_count == 5
    assert manifest.accounting.estimated_image_cost_usd == Decimal("0.40")
    assert manifest.accounting.reported_image_cost_usd == Decimal("0.211")
    assert manifest.accounting.accounted_image_cost_usd == Decimal("0.331")
    assert manifest.accounting.reported_cost_request_count == 7
    assert manifest.accounting.estimated_fallback_request_count == 3
    attempt = manifest.entries[0].provider_attempts[0]
    assert attempt.http_status == 200
    assert attempt.provider_request_id == "image-request-1"
    assert attempt.purpose.value in {"video_first_frame", "image_visual"}
    assert attempt.cost_source.value == "reported"
    assert serialize_hybrid_acquisition_manifest(manifest) == serialize_hybrid_acquisition_manifest(
        deserialize_hybrid_acquisition_manifest(serialize_hybrid_acquisition_manifest(manifest))
    )


@pytest.mark.asyncio
async def test_reference_three_request_accounting_shape() -> None:
    base = _strategy(VisualStrategyName.HYBRID_BALANCED)
    strategy = build_hybrid_visual_strategy_plan(
        job_id=base.job_id,
        source_shot_expansion_artifact_id=base.source_shot_expansion_artifact_id,
        source_shot_expansion_sha256=base.source_shot_expansion_sha256,
        source_shot_expansion_fingerprint=base.source_shot_expansion_fingerprint,
        shots=base.shots[:3],
        strategy_name=VisualStrategyName.HYBRID_BALANCED,
    )
    source = _source(strategy)
    manifest, _, _, _ = await _run(
        source,
        provider=CostedProvider(("0.031", None, "0.029")),
    )
    assert manifest.accounting is not None
    assert manifest.accounting.image_request_count == 3
    assert manifest.accounting.first_frame_request_count == 2
    assert manifest.accounting.final_image_request_count == 1
    assert manifest.accounting.estimated_image_cost_usd == Decimal("0.12")
    assert manifest.accounting.reported_image_cost_usd == Decimal("0.060")
    assert manifest.accounting.accounted_image_cost_usd == Decimal("0.100")
    assert manifest.accounting.reported_cost_request_count == 2
    assert manifest.accounting.estimated_fallback_request_count == 1


def test_invalid_float_reported_cost_is_rejected() -> None:
    with pytest.raises(ValueError, match="reported image cost"):
        ImageAcquisitionProviderResponse(
            images=(
                GeneratedImagePayload(
                    content=b"image",
                    mime_type="image/png",
                    index=0,
                ),
            ),
            provider="fixture",
            cost_usd=0.1,
            latency_ms=0,
        )


@pytest.mark.asyncio
async def test_partial_failure_persists_attempt_without_double_counting_completed_entries() -> None:
    source = _source(_strategy(VisualStrategyName.HYBRID_BALANCED))
    writer = InMemoryHybridAssetAcquisitionManifestWriter()
    provider = CountingProvider(fail_on_call=5)
    with pytest.raises(RuntimeError):
        await _run(source, provider=provider, writer=writer)
    checkpoint = await writer.read()
    assert checkpoint is not None
    assert checkpoint.accounting is not None
    assert checkpoint.accounting.image_request_count == 5
    assert len(checkpoint.entries[4].provider_attempts) == 1
    assert checkpoint.entries[4].provider_attempts[0].status.value == "failed"

    provider.fail_on_call = None
    completed = await _run(source, provider=provider, writer=writer)
    assert completed[0].accounting is not None
    assert completed[0].accounting.image_request_count == 11


@pytest.mark.asyncio
async def test_visual_intent_drift_fails_before_new_provider_call() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    source = _source(strategy)
    writer = InMemoryHybridAssetAcquisitionManifestWriter()
    provider = CountingProvider(fail_on_call=2)
    with pytest.raises(RuntimeError):
        await _run(source, provider=provider, writer=writer)
    provider.calls.clear()
    changed_assets = list(source.visual_asset_plan.assets)
    changed_assets[0] = changed_assets[0].model_copy(update={"prompt": "Changed intent"})
    drifted = source.model_copy(
        update={
            "visual_asset_plan": source.visual_asset_plan.model_copy(
                update={"assets": tuple(changed_assets)}
            )
        }
    )
    with pytest.raises(HybridAssetAcquisitionError, match="drifted"):
        await _run(drifted, provider=provider, writer=writer)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_reused_missing_and_hash_drift_fail_before_provider() -> None:
    strategy = _with_two_reused_images(_strategy(VisualStrategyName.HYBRID_BALANCED))
    source = _source(strategy)
    provider = CountingProvider()
    with pytest.raises(HybridAssetAcquisitionError, match="unavailable"):
        await _run(source, provider=provider, catalog=MemoryCatalog())
    assert provider.calls == []

    writer = InMemoryHybridAssetAcquisitionManifestWriter()
    assets = _reusable_assets(strategy)
    catalog = MemoryCatalog(assets)
    await _run(source, provider=provider, writer=writer, catalog=catalog)
    provider.calls.clear()
    first = assets[0]
    catalog.assets[first.source_asset_id] = first.model_copy(update={"sha256": "f" * 64})
    with pytest.raises(HybridAssetAcquisitionError, match="integrity drifted"):
        await _run(source, provider=provider, writer=writer, catalog=catalog)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_reused_video_requires_zero_image_requests_and_manifest_is_deterministic() -> None:
    strategy = _with_two_reused_images(_strategy(VisualStrategyName.HYBRID_BALANCED))
    shot = next(item for item in strategy.shots if item.visual_mode is VisualMode.REUSED_IMAGE)
    shots = tuple(
        item.model_copy(
            update={
                "visual_mode": VisualMode.REUSED_VIDEO,
                "motion_mode": VisualMotionMode.STATIC,
            }
        )
        if item.shot_id == shot.shot_id
        else item
        for item in strategy.shots
    )
    summary = strategy.summary.model_copy(update={"reused_image_shots": 1, "reused_video_shots": 1})
    provisional = HybridVisualStrategyPlan.model_construct(
        **strategy.model_dump(mode="python", exclude={"fingerprint", "shots", "summary"}),
        shots=shots,
        summary=summary,
        fingerprint="0" * 64,
    )
    revised = HybridVisualStrategyPlan(
        **strategy.model_dump(mode="python", exclude={"fingerprint", "shots", "summary"}),
        shots=shots,
        summary=summary,
        fingerprint=provisional.calculated_fingerprint(),
    )
    source = _source(revised)
    manifest = build_hybrid_acquisition_manifest(source)

    assert source.budget_plan.image_requests == 8
    reused_video = next(item for item in manifest.entries if item.shot_id == shot.shot_id)
    assert reused_video.image_requirement is None
    content = serialize_hybrid_acquisition_manifest(manifest)
    assert deserialize_hybrid_acquisition_manifest(content) == manifest
    assert (
        serialize_hybrid_acquisition_manifest(deserialize_hybrid_acquisition_manifest(content))
        == content
    )

    completed, provider, _, _ = await _run(source)
    resolved = next(item for item in completed.entries if item.shot_id == shot.shot_id)
    assert len(provider.calls) == 8
    assert resolved.origin is HybridAssetOrigin.REUSED_VIDEO
    assert resolved.provider_image_generated is False
    assert resolved.mime_type == "video/mp4"
