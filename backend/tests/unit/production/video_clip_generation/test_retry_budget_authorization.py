"""Offline tests for the durable video-retry budget overlay."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.cost_accounting import (
    JobCostCategory,
    derive_durable_job_cost_summary,
)
from backend.src.production.domain.duration_resolution import DurationResolutionPolicy
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.planning import (
    HybridVisualBudgetAuthorization,
    HybridVisualStrategyPolicy,
    VisualStrategyName,
    allocate_editorial_duration_plan,
    allocate_visual_shots,
    build_aggregate_visual_budget_plan,
    build_hybrid_visual_strategy_plan,
    resolve_editorial_audio_first,
)
from backend.src.production.planning.aggregate_visual_budget import (
    serialize_aggregate_visual_budget_plan,
)
from backend.src.production.scripting.models import adaptive_narrative_roles
from backend.src.production.video_clip_generation.hybrid_generation import (
    HybridVideoEntryStatus,
    HybridVideoGenerationManifest,
    HybridVideoGenerationSource,
    HybridVideoManifestStatus,
    build_hybrid_video_generation_manifest,
    serialize_hybrid_video_manifest,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterVideoRequestStatus,
    RemoteVideoJobRecord,
)
from backend.src.production.video_clip_generation.retry_budget import (
    FilesystemVideoRetryBudgetAuthorizationStore,
    VideoRetryBudgetAuthorizationError,
)
from backend.src.production.video_clip_generation.retry_budget_models import (
    deserialize_video_retry_budget_authorization,
    serialize_video_retry_budget_authorization,
)
from backend.src.production.video_clip_generation.serialization import (
    serialize_remote_video_job,
)
from backend.tests.unit.production.video_clip_generation.test_hybrid_generation import (
    EXPANSION_ID,
    JOB_ID,
    _acquisition,
)

NOW = datetime(2026, 8, 11, 22, 0, tzinfo=UTC)


def _source() -> HybridVideoGenerationSource:
    editorial = allocate_editorial_duration_plan(
        requested_duration_ms=27_000,
        scene_count=4,
        narrative_roles=adaptive_narrative_roles(4),
    )
    resolved = resolve_editorial_audio_first(
        editorial,
        (6_750, 6_750, 6_750, 6_750),
        DurationResolutionPolicy(),
    )
    shots = tuple(
        shot
        for scene in resolved.scenes
        for shot in allocate_visual_shots(scene, supported_durations_seconds=(4, 6, 8))
    )
    strategy = build_hybrid_visual_strategy_plan(
        job_id=JOB_ID,
        source_shot_expansion_artifact_id=EXPANSION_ID,
        source_shot_expansion_sha256="a" * 64,
        source_shot_expansion_fingerprint="b" * 64,
        shots=shots,
        strategy_name=VisualStrategyName.HYBRID_BALANCED,
        policy=HybridVisualStrategyPolicy(),
    )
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=HybridVisualBudgetAuthorization(
            estimated_image_cost_per_request_usd=Decimal("0.04"),
            video_price_per_second_usd=Decimal("0.03"),
            maximum_image_requests=5,
            maximum_video_requests=3,
            maximum_authorized_image_cost_usd=Decimal("0.20"),
            maximum_authorized_video_cost_per_request_usd=Decimal("0.24"),
            maximum_authorized_video_cost_usd=Decimal("0.66"),
            maximum_authorized_total_visual_cost_usd=Decimal("0.87"),
        ),
    )
    return HybridVideoGenerationSource(
        strategy_plan=strategy,
        budget_plan=budget,
        acquisition_manifest=_acquisition(strategy, budget),
    )


def _failed_manifest(source: HybridVideoGenerationSource) -> HybridVideoGenerationManifest:
    manifest = build_hybrid_video_generation_manifest(source)
    failed = next(
        entry for entry in manifest.entries if entry.status is HybridVideoEntryStatus.PENDING
    )
    entries = tuple(
        entry.model_copy(
            update={
                "status": HybridVideoEntryStatus.FAILED_TRANSIENT,
                "provider_call_count": 1,
                "error_code": "provider_transient",
            }
        )
        if entry.shot_id == failed.shot_id
        else entry
        for entry in manifest.entries
    )
    provisional = manifest.model_copy(
        update={
            "status": HybridVideoManifestStatus.FAILED,
            "entries": entries,
            "fingerprint": "0" * 64,
        }
    )
    return HybridVideoGenerationManifest.model_validate(
        provisional.model_copy(
            update={"fingerprint": provisional.calculated_fingerprint()}
        ).model_dump(mode="python")
    )


def _remote_record(
    manifest: HybridVideoGenerationManifest,
    *,
    attempt: int = 1,
    status: OpenRouterVideoRequestStatus = OpenRouterVideoRequestStatus.FAILED,
    http_status: int | None = 500,
    entry_index: int = 0,
    started_at: datetime = NOW,
) -> RemoteVideoJobRecord:
    generated = tuple(
        entry for entry in manifest.entries if entry.provider_request_identity is not None
    )
    entry = generated[entry_index]
    return RemoteVideoJobRecord(
        job_id=str(JOB_ID),
        attempt_number=attempt,
        visual_asset_id=entry.visual_asset_id,
        scene_id=entry.shot_id.rsplit("-shot-", 1)[0],
        shot_id=entry.shot_id,
        provider="openrouter",
        model="google/veo-3.1-lite",
        source_image_sha256=entry.source_asset.sha256,
        prompt_sha256="c" * 64,
        capability_snapshot_hash="d" * 64,
        provider_request_fingerprint=entry.provider_request_identity or "e" * 64,
        publication_provider="filesystem",
        publication_id=f"publication-{attempt}-{entry_index}",
        request_status=status,
        fresh_submission_permitted=False,
        prepared_at=started_at,
        submission_started_at=started_at,
        submission_http_status=http_status,
        requested_duration_seconds=entry.provider_duration_seconds,
        requested_resolution="720p",
        requested_aspect_ratio="9:16",
        generate_audio=False,
        estimated_cost_usd=entry.estimated_cost_usd,
        pricing_snapshot_at=started_at,
        pricing_sku="video-second",
    )


def _write_reference_workspace(
    root: Path, *, image_accounted: Decimal = Decimal("0.134999")
) -> tuple[HybridVideoGenerationSource, HybridVideoGenerationManifest, bytes]:
    source = _source()
    manifest = _failed_manifest(source)
    planning = root / f"production/{JOB_ID}/visual_asset_planning/attempt-1"
    video = root / f"production/{JOB_ID}/generating_video_clips/attempt-1"
    remote = video / "remote-jobs"
    acquisition = root / f"production/{JOB_ID}/acquiring_assets/attempt-1"
    planning.mkdir(parents=True)
    remote.mkdir(parents=True)
    acquisition.mkdir(parents=True)
    budget_bytes = serialize_aggregate_visual_budget_plan(source.budget_plan)
    (planning / "aggregate-visual-budget-plan.json").write_bytes(budget_bytes)
    (video / "hybrid-video-generation-manifest.json").write_bytes(
        serialize_hybrid_video_manifest(manifest)
    )
    failed = next(
        entry
        for entry in manifest.entries
        if entry.status is HybridVideoEntryStatus.FAILED_TRANSIENT
    )
    (remote / f"{failed.visual_asset_id}.json").write_bytes(
        serialize_remote_video_job(_remote_record(manifest))
    )
    per_image = image_accounted / Decimal(4)
    entries = [
        {
            "shot_id": shot.shot_id,
            "request_identity": f"request-{shot.shot_id}",
            "provider_attempts": [
                {
                    "stage_attempt_number": 1,
                    "attempt_number": 1,
                    "provider": "openrouter",
                    "estimated_cost_usd": "0.04",
                    "reported_cost_usd": str(per_image),
                }
            ],
        }
        for shot in source.strategy_plan.shots
    ]
    (acquisition / "hybrid-asset-acquisition-manifest.json").write_text(
        json.dumps({"entries": entries}, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return source, manifest, budget_bytes


async def _authorize(
    root: Path,
    *,
    ceiling: Decimal = Decimal("0.72"),
    additional_requests: int = 2,
    additional_cost: Decimal = Decimal("0.48"),
):
    return await FilesystemVideoRetryBudgetAuthorizationStore(root).authorize(
        job_id=JOB_ID,
        job_status=ProductionJobStatus.WAITING_FOR_RETRY,
        current_stage=ProductionStage.GENERATING_VIDEO_CLIPS,
        error_code="hybrid_video_provider_transient",
        source_stage_attempt=1,
        target_stage_attempt=2,
        new_authorized_video_job_cost_usd=ceiling,
        maximum_additional_provider_requests=additional_requests,
        maximum_additional_estimated_provider_cost_usd=additional_cost,
        current_settings_video_job_ceiling_usd=Decimal("0.72"),
        operator_id="operator-review",
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_reference_overlay_is_minimal_idempotent_and_preserves_budget(
    tmp_path: Path,
) -> None:
    source, _, original_budget = _write_reference_workspace(tmp_path)
    store = FilesystemVideoRetryBudgetAuthorizationStore(tmp_path)

    with pytest.raises(VideoRetryBudgetAuthorizationError, match="without an authorization"):
        await store.require_for_recovery(
            job_id=JOB_ID,
            target_stage_attempt=2,
            current_settings_video_job_ceiling_usd=Decimal("0.72"),
        )
    authorization, idempotent = await _authorize(tmp_path)
    repeated, repeated_idempotent = await _authorize(tmp_path)

    assert idempotent is False
    assert repeated_idempotent is True
    assert repeated == authorization
    assert (
        deserialize_video_retry_budget_authorization(
            serialize_video_retry_budget_authorization(authorization)
        )
        == authorization
    )
    assert authorization.original_authorized_video_job_cost_usd == Decimal("0.66")
    assert authorization.new_authorized_video_job_cost_usd == Decimal("0.72")
    assert authorization.ceiling_increase_usd == Decimal("0.06")
    assert authorization.current_accounted_video_cost_usd == Decimal("0.24")
    assert authorization.current_accounted_image_cost_usd == Decimal("0.134999")
    assert authorization.maximum_additional_estimated_provider_cost_usd == Decimal("0.48")
    assert authorization.maximum_total_provider_requests == 3
    assert authorization.projected_worst_case_visual_cost_usd == Decimal("0.854999")
    assert await store.require_for_recovery(
        job_id=JOB_ID,
        target_stage_attempt=2,
        current_settings_video_job_ceiling_usd=Decimal("0.72"),
    ) == Decimal("0.72")
    assert (
        tmp_path / f"production/{JOB_ID}/visual_asset_planning/attempt-1/"
        "aggregate-visual-budget-plan.json"
    ).read_bytes() == original_budget
    assert source.budget_plan.estimated_video_cost_usd == Decimal("0.48")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ceiling", "requests", "cost"),
    [
        (Decimal("0.71"), 2, Decimal("0.48")),
        (Decimal("0.72"), 3, Decimal("0.48")),
        (Decimal("0.72"), 2, Decimal("0.47")),
    ],
)
async def test_nonminimal_or_mismatched_authorization_fails_closed(
    tmp_path: Path, ceiling: Decimal, requests: int, cost: Decimal
) -> None:
    _write_reference_workspace(tmp_path)
    with pytest.raises(VideoRetryBudgetAuthorizationError):
        await _authorize(
            tmp_path,
            ceiling=ceiling,
            additional_requests=requests,
            additional_cost=cost,
        )


@pytest.mark.asyncio
async def test_conflicting_authorization_fails_closed(tmp_path: Path) -> None:
    _write_reference_workspace(tmp_path)
    await _authorize(tmp_path)
    with pytest.raises(VideoRetryBudgetAuthorizationError, match="conflicting"):
        await FilesystemVideoRetryBudgetAuthorizationStore(tmp_path).authorize(
            job_id=JOB_ID,
            job_status=ProductionJobStatus.WAITING_FOR_RETRY,
            current_stage=ProductionStage.GENERATING_VIDEO_CLIPS,
            error_code="hybrid_video_provider_transient",
            source_stage_attempt=1,
            target_stage_attempt=2,
            new_authorized_video_job_cost_usd=Decimal("0.72"),
            maximum_additional_provider_requests=2,
            maximum_additional_estimated_provider_cost_usd=Decimal("0.48"),
            current_settings_video_job_ceiling_usd=Decimal("0.72"),
            operator_id="different-operator",
            clock=lambda: NOW,
        )


@pytest.mark.asyncio
async def test_uncertain_submission_cannot_use_retry_overlay(tmp_path: Path) -> None:
    _, manifest, _ = _write_reference_workspace(tmp_path)
    remote = tmp_path / f"production/{JOB_ID}/generating_video_clips/attempt-1/remote-jobs"
    path = next(remote.glob("*.json"))
    path.write_bytes(
        serialize_remote_video_job(
            _remote_record(
                manifest,
                status=OpenRouterVideoRequestStatus.UNCERTAIN,
                http_status=None,
            )
        )
    )
    with pytest.raises(VideoRetryBudgetAuthorizationError, match="uncertain"):
        await _authorize(tmp_path)


@pytest.mark.asyncio
async def test_total_visual_ceiling_and_request_count_remain_bounded(
    tmp_path: Path,
) -> None:
    _write_reference_workspace(tmp_path, image_accounted=Decimal("0.16"))
    with pytest.raises(VideoRetryBudgetAuthorizationError, match="total visual"):
        await _authorize(tmp_path)


@pytest.mark.asyncio
async def test_global_settings_cannot_replace_durable_authorization(
    tmp_path: Path,
) -> None:
    _write_reference_workspace(tmp_path)
    with pytest.raises(VideoRetryBudgetAuthorizationError, match="global Settings"):
        await FilesystemVideoRetryBudgetAuthorizationStore(tmp_path).authorize(
            job_id=JOB_ID,
            job_status=ProductionJobStatus.WAITING_FOR_RETRY,
            current_stage=ProductionStage.GENERATING_VIDEO_CLIPS,
            error_code="hybrid_video_provider_transient",
            source_stage_attempt=1,
            target_stage_attempt=2,
            new_authorized_video_job_cost_usd=Decimal("0.72"),
            maximum_additional_provider_requests=2,
            maximum_additional_estimated_provider_cost_usd=Decimal("0.48"),
            current_settings_video_job_ceiling_usd=Decimal("0.66"),
            operator_id="operator-review",
            clock=lambda: NOW,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_id", "source_attempt"),
    [
        (UUID("10000000-0000-4000-8000-000000009999"), 1),
        (JOB_ID, 2),
    ],
)
async def test_wrong_job_or_source_attempt_fails_closed(
    tmp_path: Path, job_id: UUID, source_attempt: int
) -> None:
    _write_reference_workspace(tmp_path)
    with pytest.raises(VideoRetryBudgetAuthorizationError):
        await FilesystemVideoRetryBudgetAuthorizationStore(tmp_path).authorize(
            job_id=job_id,
            job_status=ProductionJobStatus.WAITING_FOR_RETRY,
            current_stage=ProductionStage.GENERATING_VIDEO_CLIPS,
            error_code="hybrid_video_provider_transient",
            source_stage_attempt=source_attempt,
            target_stage_attempt=source_attempt + 1,
            new_authorized_video_job_cost_usd=Decimal("0.72"),
            maximum_additional_provider_requests=2,
            maximum_additional_estimated_provider_cost_usd=Decimal("0.48"),
            current_settings_video_job_ceiling_usd=Decimal("0.72"),
            operator_id="operator-review",
            clock=lambda: NOW,
        )


@pytest.mark.asyncio
async def test_budget_and_manifest_drift_fail_closed(tmp_path: Path) -> None:
    _, _, _ = _write_reference_workspace(tmp_path)
    authorization, _ = await _authorize(tmp_path)
    target = (
        tmp_path / f"production/{JOB_ID}/generating_video_clips/attempt-2/"
        "video-retry-budget-authorization.json"
    )
    assert deserialize_video_retry_budget_authorization(target.read_bytes()) == authorization
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["original_video_generation_manifest_fingerprint"] = "f" * 64
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises((ValueError, VideoRetryBudgetAuthorizationError)):
        await FilesystemVideoRetryBudgetAuthorizationStore(tmp_path).require_for_recovery(
            job_id=JOB_ID,
            target_stage_attempt=2,
            current_settings_video_job_ceiling_usd=Decimal("0.72"),
        )


@pytest.mark.asyncio
async def test_authorization_is_not_spend_and_recovery_costs_are_independent(
    tmp_path: Path,
) -> None:
    _, manifest, _ = _write_reference_workspace(tmp_path)
    authorization, _ = await _authorize(tmp_path)
    before = derive_durable_job_cost_summary(
        job_id=JOB_ID, job_root=tmp_path / f"production/{JOB_ID}"
    )
    video_dir = tmp_path / f"production/{JOB_ID}/generating_video_clips/attempt-2/remote-jobs"
    video_dir.mkdir(parents=True)
    for index in range(2):
        record = _remote_record(
            manifest,
            attempt=2,
            entry_index=index,
            started_at=NOW.replace(second=index + 1),
        )
        (video_dir / f"recovery-{index}.json").write_bytes(serialize_remote_video_job(record))
    after = derive_durable_job_cost_summary(
        job_id=JOB_ID, job_root=tmp_path / f"production/{JOB_ID}"
    )

    assert before.category(JobCostCategory.VIDEO).request_count == 1
    assert before.category(JobCostCategory.VIDEO).accounted_cost_usd == Decimal("0.24")
    assert after.category(JobCostCategory.VIDEO).request_count == 3
    assert after.category(JobCostCategory.VIDEO).accounted_cost_usd == Decimal("0.72")
    assert after.visual_cost_audit is not None
    assert after.visual_cost_audit.video_budget_respected is True
    assert authorization.maximum_additional_provider_requests == 2


def test_authorization_serialization_is_strict_and_deterministic(tmp_path: Path) -> None:
    _write_reference_workspace(tmp_path)
    # The async creation path is covered above; this asserts the durable decoder itself.
    content = b'{"schema_version":"1.0.0","schema_version":"1.0.0"}'
    with pytest.raises(ValueError, match="duplicate"):
        deserialize_video_retry_budget_authorization(content)
