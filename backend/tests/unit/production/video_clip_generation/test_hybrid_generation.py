"""Offline hybrid video boundary accounting, drift, and recovery tests."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from uuid import UUID

import pytest

from backend.src.production.domain.duration_resolution import DurationResolutionPolicy
from backend.src.production.domain.visual_strategy import VisualMode, VisualMotionMode
from backend.src.production.image_acquisition.hybrid_acquisition import (
    HybridAcquisitionManifestStatus,
    HybridAssetAcquisitionEntry,
    HybridAssetAcquisitionManifest,
    HybridAssetOrigin,
    HybridAssetStatus,
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
from backend.src.production.planning.aggregate_visual_budget import AggregateVisualBudgetPlan
from backend.src.production.scripting.models import adaptive_narrative_roles
from backend.src.production.video_clip_generation.hybrid_generation import (
    GeneratedHybridVideoPayload,
    HybridResolvedAssetKind,
    HybridVideoGenerationCoordinator,
    HybridVideoGenerationError,
    HybridVideoGenerationSource,
    HybridVideoSubmissionUncertainError,
    InMemoryHybridVideoGenerationManifestWriter,
    StoredHybridVideoAsset,
    build_hybrid_video_generation_manifest,
    deserialize_hybrid_video_manifest,
    serialize_hybrid_video_manifest,
)

JOB_ID = UUID("10000000-0000-4000-8000-000000001501")
EXPANSION_ID = UUID("20000000-0000-4000-8000-000000001501")


class CountingVideoProvider:
    def __init__(self, *, fail_on_call=None, uncertain_on_call=None) -> None:
        self.calls = []
        self.posts = 0
        self.polls = 0
        self.downloads = 0
        self.fail_on_call = fail_on_call
        self.uncertain_on_call = uncertain_on_call

    async def generate_video(self, request):
        self.calls.append(request)
        self.posts += 1
        if self.uncertain_on_call == self.posts:
            raise HybridVideoSubmissionUncertainError("simulated uncertain submission")
        if self.fail_on_call == self.posts:
            raise RuntimeError("simulated transient provider failure")
        self.polls += 1
        self.downloads += 1
        return GeneratedHybridVideoPayload(
            content=f"simulated:{request.request_identity}".encode(),
            mime_type="video/mp4",
            width=720,
            height=1280,
            duration_ms=request.provider_duration_seconds * 1_000,
            provider="simulated",
            model="simulated/hybrid-video-v1",
            remote_generation_id=f"remote-{request.shot_id}",
            download_identity=hashlib.sha256(
                f"download:{request.request_identity}".encode()
            ).hexdigest(),
            reported_cost_usd=request.estimated_cost_usd,
        )


class MemoryVideoStore:
    async def store_generated(self, *, job_id, entry, payload):
        return StoredHybridVideoAsset(
            local_asset_id=f"video-{entry.visual_asset_id}",
            sha256=hashlib.sha256(payload.content).hexdigest(),
            mime_type=payload.mime_type,
            width=payload.width,
            height=payload.height,
            duration_ms=payload.duration_ms,
            storage_reference=(
                f"production/{job_id}/assets/video-clips/video-{entry.visual_asset_id}.mp4"
            ),
            provenance="orion-simulated-hybrid-video",
        )


def _shots():
    editorial = allocate_editorial_duration_plan(
        requested_duration_ms=45_000,
        scene_count=5,
        narrative_roles=adaptive_narrative_roles(5),
    )
    resolved = resolve_editorial_audio_first(
        editorial,
        (7_000, 8_500, 9_000, 10_000, 11_000),
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


def _authorization(*, maximum_video_requests=20, maximum_video_cost="3.00"):
    return HybridVisualBudgetAuthorization(
        estimated_image_cost_per_request_usd=Decimal("0.04"),
        video_price_per_second_usd=Decimal("0.03"),
        maximum_image_requests=20,
        maximum_video_requests=maximum_video_requests,
        maximum_authorized_image_cost_usd=Decimal("1.00"),
        maximum_authorized_video_cost_per_request_usd=Decimal("0.25"),
        maximum_authorized_video_cost_usd=Decimal(maximum_video_cost),
        maximum_authorized_total_visual_cost_usd=Decimal("4.00"),
    )


def _budget(strategy, *, authorization=None) -> AggregateVisualBudgetPlan:
    return build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=authorization or _authorization(),
    )


def _acquisition(strategy, budget) -> HybridAssetAcquisitionManifest:
    requirements = {item.shot_id: item.requirement for item in budget.image_requirements}
    entries = []
    for shot in strategy.shots:
        reused_video = shot.visual_mode is VisualMode.REUSED_VIDEO
        reused = shot.visual_mode in {VisualMode.REUSED_VIDEO, VisualMode.REUSED_IMAGE}
        generated = shot.visual_mode in {
            VisualMode.GENERATED_VIDEO,
            VisualMode.GENERATED_IMAGE,
        }
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
                image_requirement=requirements.get(shot.shot_id),
                strategy_fingerprint=strategy.fingerprint,
                budget_fingerprint=budget.fingerprint,
                request_identity=hashlib.sha256(f"acquire:{shot.shot_id}".encode()).hexdigest(),
                status=HybridAssetStatus.RESOLVED,
                provider_image_generated=generated,
                reused=reused,
                local_asset_id=f"{'reused' if reused else 'generated'}-{shot.visual_asset_id}",
                sha256=hashlib.sha256(
                    f"asset:{shot.shot_id}:{shot.visual_mode.value}".encode()
                ).hexdigest(),
                mime_type="video/mp4" if reused_video else "image/png",
                width=None if reused_video else 720,
                height=None if reused_video else 1280,
                storage_reference=f"production/{JOB_ID}/assets/{shot.visual_asset_id}",
                provenance="offline-hybrid-fixture",
            )
        )
    provisional = HybridAssetAcquisitionManifest.model_construct(
        job_id=JOB_ID,
        source_visual_asset_plan_sha256="c" * 64,
        strategy_fingerprint=strategy.fingerprint,
        budget_fingerprint=budget.fingerprint,
        status=HybridAcquisitionManifestStatus.COMPLETED,
        entries=tuple(entries),
        fingerprint="0" * 64,
    )
    return HybridAssetAcquisitionManifest(
        job_id=JOB_ID,
        source_visual_asset_plan_sha256="c" * 64,
        strategy_fingerprint=strategy.fingerprint,
        budget_fingerprint=budget.fingerprint,
        status=HybridAcquisitionManifestStatus.COMPLETED,
        entries=tuple(entries),
        fingerprint=provisional.calculated_fingerprint(),
    )


def _source(name) -> HybridVideoGenerationSource:
    strategy = _strategy(name)
    budget = _budget(strategy)
    return HybridVideoGenerationSource(
        strategy_plan=strategy,
        budget_plan=budget,
        acquisition_manifest=_acquisition(strategy, budget),
    )


def _with_reused_video(strategy) -> HybridVisualStrategyPlan:
    selected = next(
        shot for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_VIDEO
    )
    shots = tuple(
        shot.model_copy(
            update={
                "visual_mode": VisualMode.REUSED_VIDEO,
                "motion_mode": VisualMotionMode.STATIC,
                "source_asset_id": f"reuse:{shot.shot_id}",
                "provider_duration_seconds": None,
            }
        )
        if shot.shot_id == selected.shot_id
        else shot
        for shot in strategy.shots
    )
    summary = HybridVisualStrategySummary(
        visual_shot_count=10,
        generated_video_shots=strategy.summary.generated_video_shots - 1,
        generated_image_shots=strategy.summary.generated_image_shots,
        reused_video_shots=1,
        reused_image_shots=0,
        quality_floor_pass=True,
        quality_degradation_authorized=False,
        maximum_consecutive_image_shots=strategy.summary.maximum_consecutive_image_shots,
    )
    provisional = strategy.model_copy(
        update={"shots": shots, "summary": summary, "fingerprint": "0" * 64}
    )
    return strategy.model_copy(
        update={
            "shots": shots,
            "summary": summary,
            "fingerprint": provisional.calculated_fingerprint(),
        }
    )


def _with_reused_image(strategy) -> HybridVisualStrategyPlan:
    selected = next(
        shot for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_IMAGE
    )
    shots = tuple(
        shot.model_copy(
            update={
                "visual_mode": VisualMode.REUSED_IMAGE,
                "source_asset_id": f"reuse:{shot.shot_id}",
            }
        )
        if shot.shot_id == selected.shot_id
        else shot
        for shot in strategy.shots
    )
    summary = strategy.summary.model_copy(
        update={
            "generated_image_shots": strategy.summary.generated_image_shots - 1,
            "reused_image_shots": 1,
        }
    )
    provisional = strategy.model_copy(
        update={"shots": shots, "summary": summary, "fingerprint": "0" * 64}
    )
    return strategy.model_copy(
        update={
            "shots": shots,
            "summary": summary,
            "fingerprint": provisional.calculated_fingerprint(),
        }
    )


async def _execute(source, *, provider=None, writer=None):
    effective_provider = provider or CountingVideoProvider()
    effective_writer = writer or InMemoryHybridVideoGenerationManifestWriter()
    manifest = await HybridVideoGenerationCoordinator(
        provider=effective_provider,
        store=MemoryVideoStore(),
        manifest_writer=effective_writer,
    ).execute(source)
    return manifest, effective_provider, effective_writer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy_name", "calls", "seconds"),
    (
        (VisualStrategyName.HYBRID_BALANCED, 5, 34),
        (VisualStrategyName.HYBRID_ECONOMY, 3, 20),
        (VisualStrategyName.FULL_VIDEO, 10, 54),
    ),
)
async def test_executes_only_authorized_generated_video_requirements(
    strategy_name, calls, seconds
) -> None:
    source = _source(strategy_name)
    manifest, provider, _ = await _execute(source)

    assert provider.posts == calls
    assert sum(item.provider_duration_seconds for item in provider.calls) == seconds
    assert tuple(item.provider_duration_seconds for item in provider.calls) == tuple(
        item.provider_duration_seconds for item in source.budget_plan.video_requirements
    )
    assert sum(
        entry.resolved_asset.kind is HybridResolvedAssetKind.VIDEO
        for entry in manifest.entries
    ) == calls


@pytest.mark.asyncio
async def test_balanced_boundary_outputs_five_videos_and_five_images() -> None:
    manifest, provider, _ = await _execute(_source(VisualStrategyName.HYBRID_BALANCED))
    assert provider.posts == 5
    assert sum(entry.resolved_asset.kind is HybridResolvedAssetKind.VIDEO for entry in manifest.entries) == 5
    assert sum(entry.resolved_asset.kind is HybridResolvedAssetKind.IMAGE for entry in manifest.entries) == 5
    assert all(
        entry.resolved_asset.remote_status.value == "completed"
        and entry.resolved_asset.download_identity is not None
        for entry in manifest.entries
        if entry.visual_mode is VisualMode.GENERATED_VIDEO
    )
    assert all(
        entry.provider_call_count == 0
        for entry in manifest.entries
        if entry.visual_mode is not VisualMode.GENERATED_VIDEO
    )


@pytest.mark.asyncio
async def test_reused_video_resolves_without_provider_generation() -> None:
    strategy = _with_reused_video(_strategy(VisualStrategyName.HYBRID_BALANCED))
    budget = _budget(strategy)
    source = HybridVideoGenerationSource(
        strategy_plan=strategy,
        budget_plan=budget,
        acquisition_manifest=_acquisition(strategy, budget),
    )
    manifest, provider, _ = await _execute(source)
    reused = next(entry for entry in manifest.entries if entry.visual_mode is VisualMode.REUSED_VIDEO)

    assert provider.posts == 4
    assert reused.resolved_asset.provider_generated is False
    assert reused.provider_duration_seconds is None
    assert reused.estimated_cost_usd == 0


@pytest.mark.asyncio
async def test_reused_and_generated_images_make_zero_video_calls() -> None:
    strategy = _with_reused_image(_strategy(VisualStrategyName.HYBRID_BALANCED))
    budget = _budget(strategy)
    source = HybridVideoGenerationSource(
        strategy_plan=strategy,
        budget_plan=budget,
        acquisition_manifest=_acquisition(strategy, budget),
    )
    manifest, provider, _ = await _execute(source)

    assert provider.posts == 5
    assert all(
        entry.provider_call_count == 0
        and entry.video_requirement_identity is None
        and entry.provider_request_identity is None
        for entry in manifest.entries
        if entry.visual_mode in {VisualMode.GENERATED_IMAGE, VisualMode.REUSED_IMAGE}
    )


@pytest.mark.asyncio
async def test_budget_rejection_fails_before_first_provider_call() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    budget = _budget(
        strategy,
        authorization=_authorization(maximum_video_requests=0, maximum_video_cost="0"),
    )
    source = HybridVideoGenerationSource(
        strategy_plan=strategy,
        budget_plan=budget,
        acquisition_manifest=_acquisition(strategy, budget),
    )
    provider = CountingVideoProvider()
    with pytest.raises(HybridVideoGenerationError, match="not authorized"):
        await _execute(source, provider=provider)
    assert provider.posts == 0


@pytest.mark.asyncio
async def test_partial_recovery_reuses_completed_video_entries() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    writer = InMemoryHybridVideoGenerationManifestWriter()
    first = CountingVideoProvider(fail_on_call=3)
    with pytest.raises(HybridVideoGenerationError, match="transient"):
        await _execute(source, provider=first, writer=writer)
    retry = CountingVideoProvider()
    completed, retry, _ = await _execute(source, provider=retry, writer=writer)

    assert first.posts == 3
    assert retry.posts == 3
    assert completed.status.value == "completed"
    assert [
        entry.provider_call_count
        for entry in completed.entries
        if entry.visual_mode is VisualMode.GENERATED_VIDEO
    ] == [1, 1, 2, 1, 1]


@pytest.mark.asyncio
async def test_completed_manifest_is_reused_without_provider_calls() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    writer = InMemoryHybridVideoGenerationManifestWriter()
    completed, _, _ = await _execute(source, writer=writer)
    retry = CountingVideoProvider()
    reused, retry, _ = await _execute(source, provider=retry, writer=writer)

    assert retry.posts == 0
    assert reused == completed


@pytest.mark.asyncio
async def test_uncertain_submission_never_resubmits_without_reconciliation() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    writer = InMemoryHybridVideoGenerationManifestWriter()
    with pytest.raises(HybridVideoGenerationError, match="uncertain"):
        await _execute(
            source,
            provider=CountingVideoProvider(uncertain_on_call=1),
            writer=writer,
        )
    retry = CountingVideoProvider()
    with pytest.raises(HybridVideoGenerationError, match="requires reconciliation"):
        await _execute(source, provider=retry, writer=writer)
    assert retry.posts == 0


@pytest.mark.asyncio
async def test_recovery_first_frame_sha_drift_fails_before_new_call() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    writer = InMemoryHybridVideoGenerationManifestWriter()
    with pytest.raises(HybridVideoGenerationError):
        await _execute(source, provider=CountingVideoProvider(fail_on_call=2), writer=writer)
    entries = list(source.acquisition_manifest.entries)
    index = next(
        index
        for index, entry in enumerate(entries)
        if entry.visual_mode is VisualMode.GENERATED_VIDEO
    )
    entries[index] = entries[index].model_copy(update={"sha256": "f" * 64})
    provisional = source.acquisition_manifest.model_copy(
        update={"entries": tuple(entries), "fingerprint": "0" * 64}
    )
    acquisition = source.acquisition_manifest.model_copy(
        update={"entries": tuple(entries), "fingerprint": provisional.calculated_fingerprint()}
    )
    retry = CountingVideoProvider()
    with pytest.raises(HybridVideoGenerationError, match="recovery source drifted"):
        await _execute(
            source.model_copy(update={"acquisition_manifest": acquisition}),
            provider=retry,
            writer=writer,
        )
    assert retry.posts == 0


@pytest.mark.asyncio
async def test_video_requirement_identity_drift_fails_closed_on_recovery() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    writer = InMemoryHybridVideoGenerationManifestWriter()
    expected = build_hybrid_video_generation_manifest(source)
    entries = list(expected.entries)
    index = next(
        index
        for index, entry in enumerate(entries)
        if entry.visual_mode is VisualMode.GENERATED_VIDEO
    )
    entries[index] = entries[index].model_copy(
        update={"video_requirement_identity": "f" * 64}
    )
    provisional = expected.model_copy(
        update={"entries": tuple(entries), "fingerprint": "0" * 64}
    )
    drifted = expected.model_copy(
        update={"entries": tuple(entries), "fingerprint": provisional.calculated_fingerprint()}
    )
    writer.content = serialize_hybrid_video_manifest(drifted)
    provider = CountingVideoProvider()

    with pytest.raises(HybridVideoGenerationError, match="recovery entry drifted"):
        await _execute(source, provider=provider, writer=writer)
    assert provider.posts == 0


@pytest.mark.asyncio
async def test_strategy_budget_mismatch_fails_before_provider() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    provider = CountingVideoProvider()
    with pytest.raises(HybridVideoGenerationError, match="budget does not pin"):
        await _execute(
            source.model_copy(
                update={"strategy_plan": _strategy(VisualStrategyName.HYBRID_ECONOMY)}
            ),
            provider=provider,
        )
    assert provider.posts == 0


def test_manifest_serialization_is_strict_and_deterministic() -> None:
    manifest = build_hybrid_video_generation_manifest(
        _source(VisualStrategyName.HYBRID_BALANCED)
    )
    content = serialize_hybrid_video_manifest(manifest)
    assert serialize_hybrid_video_manifest(deserialize_hybrid_video_manifest(content)) == content
    with pytest.raises(ValueError, match="duplicate JSON key"):
        deserialize_hybrid_video_manifest(b'{"job_id":"x","job_id":"y"}')


def test_provider_requests_pin_all_upstream_identities() -> None:
    source = _source(VisualStrategyName.HYBRID_BALANCED)
    entries = build_hybrid_video_generation_manifest(source).entries
    generated = tuple(
        entry for entry in entries if entry.visual_mode is VisualMode.GENERATED_VIDEO
    )
    images = tuple(
        entry for entry in entries if entry.visual_mode is VisualMode.GENERATED_IMAGE
    )

    assert len(generated) == 5
    assert all(entry.source_asset.mime_type.startswith("image/") for entry in generated)
    assert all(entry.video_requirement_identity for entry in generated)
    assert all(entry.provider_request_identity for entry in generated)
    assert all(
        entry.resolved_asset is None for entry in generated
    )
    assert all(entry.acquisition_fingerprint == source.acquisition_manifest.fingerprint for entry in generated)
    assert all(entry.provider_duration_seconds is None for entry in images)
    assert all(entry.video_requirement_identity is None for entry in images)
    assert all(entry.estimated_cost_usd == 0 for entry in images)
