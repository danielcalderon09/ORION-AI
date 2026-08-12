"""Offline hybrid strategy and aggregate visual budget planning tests."""

from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.domain.duration_resolution import DurationResolutionPolicy
from backend.src.production.domain.visual_strategy import (
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
)
from backend.src.production.planning import (
    AggregateVisualBudgetError,
    HybridVisualBudgetAuthorization,
    HybridVisualStrategyPlan,
    HybridVisualStrategyPolicy,
    VisualStrategyName,
    VisualStrategyQualityError,
    allocate_editorial_duration_plan,
    allocate_visual_shots,
    authorize_aggregate_visual_budget,
    build_aggregate_visual_budget_plan,
    build_hybrid_visual_strategy_plan,
    deserialize_aggregate_visual_budget_plan,
    deserialize_hybrid_visual_strategy_plan,
    resolve_editorial_audio_first,
    serialize_aggregate_visual_budget_plan,
    serialize_hybrid_visual_strategy_plan,
)
from backend.src.production.planning.provider_budget_planner import (
    VisualShotAllocation,
    VisualShotFunction,
)
from backend.src.production.scripting.models import adaptive_narrative_roles

JOB_ID = UUID("10000000-0000-4000-8000-000000001201")
EXPANSION_ID = UUID("20000000-0000-4000-8000-000000001201")


def _shots(
    *,
    target_ms: int = 45_000,
    narration_ms: tuple[int, ...] = (7_000, 8_500, 9_000, 10_000, 11_000),
) -> tuple[VisualShotAllocation, ...]:
    editorial = allocate_editorial_duration_plan(
        requested_duration_ms=target_ms,
        scene_count=len(narration_ms),
        narrative_roles=adaptive_narrative_roles(len(narration_ms)),
    )
    resolved = resolve_editorial_audio_first(
        editorial,
        narration_ms,
        DurationResolutionPolicy(),
    )
    return tuple(
        shot
        for scene in resolved.scenes
        for shot in allocate_visual_shots(
            scene,
            supported_durations_seconds=(4, 6, 8),
        )
    )


def _strategy(
    name: VisualStrategyName,
    *,
    shots: tuple[VisualShotAllocation, ...] | None = None,
    policy: HybridVisualStrategyPolicy | None = None,
) -> HybridVisualStrategyPlan:
    return build_hybrid_visual_strategy_plan(
        job_id=JOB_ID,
        source_shot_expansion_artifact_id=EXPANSION_ID,
        source_shot_expansion_sha256="a" * 64,
        source_shot_expansion_fingerprint="b" * 64,
        shots=shots or _shots(),
        strategy_name=name,
        policy=policy,
    )


def _authorization(
    *,
    maximum_image_requests: int = 10,
    maximum_video_requests: int = 10,
    maximum_image_cost: str = "0.40",
    maximum_video_cost_per_request: str = "0.25",
    maximum_video_cost: str = "2.00",
    maximum_total_cost: str = "2.10",
) -> HybridVisualBudgetAuthorization:
    return HybridVisualBudgetAuthorization(
        estimated_image_cost_per_request_usd=Decimal("0.04"),
        video_price_per_second_usd=Decimal("0.03"),
        maximum_image_requests=maximum_image_requests,
        maximum_video_requests=maximum_video_requests,
        maximum_authorized_image_cost_usd=Decimal(maximum_image_cost),
        maximum_authorized_video_cost_per_request_usd=Decimal(
            maximum_video_cost_per_request
        ),
        maximum_authorized_video_cost_usd=Decimal(maximum_video_cost),
        maximum_authorized_total_visual_cost_usd=Decimal(maximum_total_cost),
    )


def _synthetic_shots(count: int) -> tuple[VisualShotAllocation, ...]:
    roles = adaptive_narrative_roles(count)
    return tuple(
        VisualShotAllocation(
            scene_id=f"scene-{index + 1:03d}",
            shot_id=f"scene-{index + 1:03d}-shot-001",
            shot_sequence_index=0,
            visual_asset_id=f"asset-s{index + 1:03d}-q001-v001",
            narrative_role=role,
            visual_function=VisualShotFunction.PRIMARY,
            intent_key=f"scene-{index + 1:03d}:{role.value}:primary",
            usable_duration_ms=4_000,
            provider_duration_seconds=4,
        )
        for index, role in enumerate(roles)
    )


def _educational_api_shots() -> tuple[VisualShotAllocation, ...]:
    roles = adaptive_narrative_roles(5)
    layout = (
        (1, 1, 0, roles[0], VisualShotFunction.ESTABLISH),
        (1, 2, 1, roles[0], VisualShotFunction.ADVANCE),
        (2, 1, 0, roles[1], VisualShotFunction.PRIMARY),
        (3, 1, 0, roles[2], VisualShotFunction.REVEAL),
        (4, 1, 0, roles[3], VisualShotFunction.ADVANCE),
        (5, 1, 0, roles[4], VisualShotFunction.RESOLVE),
    )
    return tuple(
        VisualShotAllocation(
            scene_id=f"scene-{scene:03d}",
            shot_id=f"scene-{scene:03d}-shot-{shot:03d}",
            shot_sequence_index=sequence,
            visual_asset_id=f"asset-s{scene:03d}-q{shot:03d}-v001",
            narrative_role=role,
            visual_function=function,
            intent_key=f"api:{scene}:{shot}:{function.value}",
            usable_duration_ms=5_000,
            provider_duration_seconds=6,
        )
        for scene, shot, sequence, role, function in layout
    )


@pytest.mark.parametrize(
    ("name", "videos", "seconds", "images", "total"),
    (
        (VisualStrategyName.FULL_VIDEO, 10, 54, 10, Decimal("2.02")),
        (VisualStrategyName.HYBRID_BALANCED, 5, 34, 10, Decimal("1.42")),
        (VisualStrategyName.HYBRID_ECONOMY, 3, 20, 10, Decimal("1.00")),
    ),
)
def test_reference_45s_strategy_and_cost_accounting(
    name: VisualStrategyName,
    videos: int,
    seconds: int,
    images: int,
    total: Decimal,
) -> None:
    strategy = _strategy(name)
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(),
    )

    assert strategy.summary.visual_shot_count == 10
    assert strategy.summary.generated_video_shots == videos
    assert budget.video_requests == videos
    assert budget.purchased_video_seconds == seconds
    assert budget.image_requests == images
    assert budget.estimated_total_visual_cost_usd == total
    assert budget.budget_pass is True
    assert len({item.shot_id for item in budget.image_requirements}) == images
    assert {item.shot_id for item in budget.video_requirements} == {
        shot.shot_id
        for shot in strategy.shots
        if shot.visual_mode is VisualMode.GENERATED_VIDEO
    }


def test_image_only_api_fixture_is_all_images_with_zero_video_exposure() -> None:
    strategy = _strategy(
        VisualStrategyName.IMAGE_ONLY,
        shots=_educational_api_shots(),
    )
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(
            maximum_image_requests=6,
            maximum_video_requests=0,
            maximum_image_cost="0.24",
            maximum_video_cost_per_request="0",
            maximum_video_cost="0",
            maximum_total_cost="0.24",
        ),
    )

    assert strategy.summary.visual_shot_count == 6
    assert strategy.summary.generated_image_shots == 6
    assert strategy.summary.generated_video_shots == 0
    assert strategy.summary.reused_image_shots == 0
    assert strategy.summary.reused_video_shots == 0
    assert strategy.summary.quality_floor_pass is True
    assert all(shot.visual_mode is VisualMode.GENERATED_IMAGE for shot in strategy.shots)
    assert all(shot.source_asset_id is None for shot in strategy.shots)
    assert all(shot.provider_duration_seconds is None for shot in strategy.shots)
    assert len({shot.motion_mode for shot in strategy.shots}) > 1
    assert budget.image_requests == 6
    assert budget.video_requests == 0
    assert budget.purchased_video_seconds == 0
    assert budget.estimated_image_cost_usd == Decimal("0.24")
    assert budget.estimated_video_cost_usd == Decimal("0")
    assert budget.estimated_total_visual_cost_usd == Decimal("0.24")
    assert {item.requirement.value for item in budget.image_requirements} == {
        "image_visual"
    }
    assert budget.budget_pass is True


def test_image_only_image_and_total_gates_fail_but_zero_video_gate_passes() -> None:
    strategy = _strategy(
        VisualStrategyName.IMAGE_ONLY,
        shots=_educational_api_shots(),
    )
    image_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(
            maximum_image_requests=5,
            maximum_video_requests=0,
            maximum_video_cost_per_request="0",
            maximum_video_cost="0",
        ),
    )
    total_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(
            maximum_image_requests=6,
            maximum_video_requests=0,
            maximum_image_cost="0.24",
            maximum_video_cost_per_request="0",
            maximum_video_cost="0",
            maximum_total_cost="0.23",
        ),
    )

    assert image_rejected.video_requests == 0
    assert image_rejected.budget_pass is False
    assert total_rejected.video_requests == 0
    assert total_rejected.budget_pass is False


def test_strategy_comparison_preserves_existing_allocations() -> None:
    shots = _educational_api_shots()
    counts = {
        name: _strategy(name, shots=shots).summary.generated_video_shots
        for name in VisualStrategyName
    }

    assert counts == {
        VisualStrategyName.FULL_VIDEO: 6,
        VisualStrategyName.HYBRID_BALANCED: 3,
        VisualStrategyName.HYBRID_ECONOMY: 2,
        VisualStrategyName.IMAGE_ONLY: 0,
    }


def test_balanced_quality_floor_uses_one_video_per_scene_and_varied_image_motion() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    video = tuple(
        shot for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_VIDEO
    )
    images = tuple(
        shot for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_IMAGE
    )

    assert {shot.scene_id for shot in video} == {
        "scene-001",
        "scene-002",
        "scene-003",
        "scene-004",
        "scene-005",
    }
    assert all(shot.shot_sequence_index == 0 for shot in video)
    assert {shot.motion_mode for shot in images} == {
        VisualMotionMode.ZOOM_IN,
        VisualMotionMode.ZOOM_OUT,
    }
    assert strategy.summary.quality_floor_pass is True
    assert strategy.summary.maximum_consecutive_image_shots == 1


def test_economy_preserves_hook_and_two_high_impact_moments() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_ECONOMY)
    video = tuple(
        shot for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_VIDEO
    )

    assert tuple(shot.scene_id for shot in video) == (
        "scene-001",
        "scene-004",
        "scene-005",
    )
    assert strategy.summary.generated_video_shots == 3
    assert strategy.summary.quality_floor_pass is True


def test_two_shot_balanced_short_preserves_one_hook_video() -> None:
    strategy = _strategy(
        VisualStrategyName.HYBRID_BALANCED,
        shots=_shots(target_ms=8_000, narration_ms=(4_000, 4_000)),
    )

    assert strategy.summary.visual_shot_count == 2
    assert strategy.summary.generated_video_shots == 1
    video = next(
        shot for shot in strategy.shots if shot.visual_mode is VisualMode.GENERATED_VIDEO
    )
    assert video.scene_id == "scene-001"
    assert strategy.summary.quality_floor_pass is True


@pytest.mark.parametrize(
    ("shot_count", "balanced_videos", "economy_videos"),
    ((2, 1, 1), (5, 3, 2), (10, 5, 3), (20, 10, 6), (24, 12, 8)),
)
def test_strategy_ratios_scale_without_hardcoded_ten_shot_assumption(
    shot_count: int,
    balanced_videos: int,
    economy_videos: int,
) -> None:
    shots = _synthetic_shots(shot_count)
    balanced = _strategy(VisualStrategyName.HYBRID_BALANCED, shots=shots)
    economy = _strategy(VisualStrategyName.HYBRID_ECONOMY, shots=shots)

    assert balanced.summary.generated_video_shots == balanced_videos
    assert economy.summary.generated_video_shots == economy_videos
    assert balanced.summary.quality_floor_pass is True
    assert economy.summary.quality_floor_pass is True
    assert balanced.summary.maximum_consecutive_image_shots <= 3


def test_all_hero_required_shots_remain_bounded_and_budget_is_authority() -> None:
    promoted = tuple(
        VisualShotAllocation.model_validate(
            {
                **shot.model_dump(mode="python"),
                "importance": VisualImportance.HERO,
                "generation_priority": VisualGenerationPriority.REQUIRED,
            }
        )
        for shot in _shots()
    )
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED, shots=promoted)
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(maximum_video_requests=4),
    )

    assert strategy.summary.generated_video_shots == 5
    assert budget.budget_pass is False
    with pytest.raises(AggregateVisualBudgetError):
        authorize_aggregate_visual_budget(budget)


def test_typed_priority_changes_only_its_stable_balanced_selection_bucket() -> None:
    shots = list(_shots())
    promoted = shots[3]
    shots[3] = VisualShotAllocation.model_validate(
        {
            **promoted.model_dump(mode="python"),
            "importance": VisualImportance.HERO,
            "generation_priority": VisualGenerationPriority.REQUIRED,
        }
    )
    strategy = _strategy(
        VisualStrategyName.HYBRID_BALANCED,
        shots=tuple(shots),
    )

    selected = {
        shot.shot_id
        for shot in strategy.shots
        if shot.visual_mode is VisualMode.GENERATED_VIDEO
    }
    assert promoted.shot_id in selected
    assert "scene-002-shot-001" not in selected
    assert len(selected) == 5


def test_zero_video_policy_requires_explicit_quality_degradation() -> None:
    short = _shots(target_ms=8_000, narration_ms=(4_000, 4_000))
    with pytest.raises(VisualStrategyQualityError):
        _strategy(
            VisualStrategyName.HYBRID_BALANCED,
            shots=short,
            policy=HybridVisualStrategyPolicy(maximum_generated_video_shots=0),
        )

    strategy = _strategy(
        VisualStrategyName.HYBRID_BALANCED,
        shots=short,
        policy=HybridVisualStrategyPolicy(
            maximum_generated_video_shots=0,
            allow_quality_degradation=True,
        ),
    )
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(
            maximum_image_requests=2,
            maximum_video_requests=0,
            maximum_image_cost="0.08",
            maximum_video_cost="0",
            maximum_total_cost="0.08",
        ),
    )

    assert strategy.summary.generated_video_shots == 0
    assert strategy.summary.quality_floor_pass is False
    assert strategy.summary.quality_degradation_authorized is True
    assert budget.video_requests == 0
    assert budget.image_requests == 2
    assert budget.budget_pass is True


def test_small_total_budget_and_low_image_limit_fail_before_authorization() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    total_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(maximum_total_cost="1.41"),
    )
    images_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(maximum_image_requests=9),
    )

    assert total_rejected.estimated_image_cost_usd == Decimal("0.40")
    assert total_rejected.estimated_video_cost_usd == Decimal("1.02")
    assert total_rejected.budget_pass is False
    assert images_rejected.budget_pass is False
    with pytest.raises(AggregateVisualBudgetError):
        authorize_aggregate_visual_budget(total_rejected)


def test_per_request_and_video_job_cost_gates_remain_independent() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    per_request_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(maximum_video_cost_per_request="0.20"),
    )
    job_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(maximum_video_cost="1.01"),
    )

    assert max(
        item.estimated_cost_usd for item in per_request_rejected.video_requirements
    ) == Decimal("0.24")
    assert per_request_rejected.budget_pass is False
    assert job_rejected.estimated_video_cost_usd == Decimal("1.02")
    assert job_rejected.budget_pass is False


def test_reuse_is_preserved_only_with_known_source_and_reduces_image_count() -> None:
    shots = list(_shots())
    source = shots[-1]
    shots[-1] = VisualShotAllocation.model_validate(
        {
            **source.model_dump(mode="python"),
            "visual_mode": VisualMode.REUSED_IMAGE,
            "motion_mode": VisualMotionMode.ZOOM_OUT,
            "source_asset_id": "asset-library-sun-001",
            "provider_duration_seconds": None,
        }
    )
    strategy = _strategy(
        VisualStrategyName.HYBRID_BALANCED,
        shots=tuple(shots),
    )
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(),
    )

    assert strategy.summary.reused_image_shots == 1
    assert strategy.shots[-1].source_asset_id == "asset-library-sun-001"
    assert budget.image_requests == 9
    assert all(item.shot_id != strategy.shots[-1].shot_id for item in budget.image_requirements)


def test_input_permutations_produce_identical_canonical_plan_and_fingerprint() -> None:
    shots = _shots()
    first = _strategy(VisualStrategyName.HYBRID_BALANCED, shots=shots)
    second = _strategy(
        VisualStrategyName.HYBRID_BALANCED,
        shots=tuple(reversed(shots)),
    )

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert serialize_hybrid_visual_strategy_plan(first) == (
        serialize_hybrid_visual_strategy_plan(second)
    )


def test_strategy_and_budget_serialization_are_deterministic_and_tamper_evident() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_ECONOMY)
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(),
    )
    strategy_bytes = serialize_hybrid_visual_strategy_plan(strategy)
    budget_bytes = serialize_aggregate_visual_budget_plan(budget)

    assert deserialize_hybrid_visual_strategy_plan(strategy_bytes) == strategy
    assert deserialize_aggregate_visual_budget_plan(budget_bytes) == budget
    assert strategy_bytes == serialize_hybrid_visual_strategy_plan(strategy)
    assert budget_bytes == serialize_aggregate_visual_budget_plan(budget)

    payload = strategy.model_dump(mode="json")
    payload["shots"][0]["usable_duration_ms"] += 1
    with pytest.raises(ValidationError):
        HybridVisualStrategyPlan.model_validate(payload)


def test_generated_video_first_frame_is_not_double_counted_as_generated_image() -> None:
    strategy = _strategy(VisualStrategyName.HYBRID_BALANCED)
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_authorization(),
    )

    assert budget.image_requests == (
        strategy.summary.generated_video_shots
        + strategy.summary.generated_image_shots
    )
    assert budget.image_requests == len(strategy.shots)
    assert budget.video_requests == strategy.summary.generated_video_shots
