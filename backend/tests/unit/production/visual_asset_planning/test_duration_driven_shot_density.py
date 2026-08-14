"""Duration-driven image-only shot density and budget boundary tests."""

from decimal import Decimal
from uuid import UUID

import pytest

from backend.src.production.domain.duration_resolution import (
    DurableDurationResolution,
    ResolvedSceneDuration,
)
from backend.src.production.domain.visual_strategy import VisualMode
from backend.src.production.planning.aggregate_visual_budget import (
    AggregateVisualBudgetError,
    HybridVisualBudgetAuthorization,
    authorize_aggregate_visual_budget,
    build_aggregate_visual_budget_plan,
)
from backend.src.production.planning.visual_strategy import (
    VisualStrategyName,
    build_hybrid_visual_strategy_plan,
)
from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScene,
    ProductionScenePlan,
    ProductionShot,
    ProductionTiming,
    ProductionTransition,
)
from backend.src.production.scripting.models import NarrativeRole, StoryBeat
from backend.src.production.visual_asset_planning.shot_expansion import (
    PostTtsShotExpansion,
    VisualShotDensityPolicy,
    build_post_tts_shot_expansion,
)

JOB_ID = UUID("10000000-0000-4000-8000-000000001301")
SCENE_ARTIFACT_ID = UUID("20000000-0000-4000-8000-000000001301")
DURATION_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000001301")
EXPANSION_ARTIFACT_ID = UUID("40000000-0000-4000-8000-000000001301")


def _inputs(scene_durations_ms: tuple[int, ...]) -> tuple[
    ProductionScenePlan,
    DurableDurationResolution,
]:
    scenes: list[ProductionScene] = []
    resolved: list[ResolvedSceneDuration] = []
    for index, duration_ms in enumerate(scene_durations_ms):
        scene_number = index + 1
        scene_id = f"scene-{scene_number:03d}"
        duration_seconds = duration_ms / 1_000
        role = NarrativeRole.HOOK if index == 0 else NarrativeRole.DEVELOPMENT
        scenes.append(
            ProductionScene(
                scene_id=scene_id,
                scene_number=scene_number,
                source_scene_number=scene_number,
                title=f"Scene {scene_number}",
                narration=f"Narration for scene {scene_number}.",
                objective=f"Represent scene {scene_number}",
                story_beat=StoryBeat(
                    role=role,
                    information_introduced=f"Information {scene_number}",
                    prior_context="Prior context",
                    new_information=f"New information {scene_number}",
                    open_question="What follows?",
                    transition_intent="Continue",
                ),
                estimated_duration_seconds=duration_seconds,
                shots=(
                    ProductionShot(
                        shot_id=f"{scene_id}-shot-001",
                        shot_number=1,
                        scene_number=scene_number,
                        objective=f"Show scene {scene_number}",
                        description=f"Visual for scene {scene_number}",
                        camera=ProductionCamera(
                            framing="wide",
                            movement="static",
                            subject=f"subject {scene_number}",
                        ),
                        timing=ProductionTiming(
                            start_seconds=0,
                            duration_seconds=duration_seconds,
                            end_seconds=duration_seconds,
                        ),
                        transition=ProductionTransition(
                            kind=(
                                "none"
                                if index == len(scene_durations_ms) - 1
                                else "cut"
                            )
                        ),
                    ),
                ),
            )
        )
        resolved.append(
            ResolvedSceneDuration(
                scene_id=scene_id,
                sequence_index=index,
                planned_duration_ms=duration_ms,
                actual_narration_duration_ms=duration_ms,
                resolved_duration_ms=duration_ms,
            )
        )
    total_ms = sum(scene_durations_ms)
    return (
        ProductionScenePlan(
            source_script_schema_version="1.0.0",
            source_script_sha256="a" * 64,
            title="Duration density",
            language="es",
            target_duration_seconds=total_ms / 1_000,
            scenes=tuple(scenes),
        ),
        DurableDurationResolution(
            requested_target_duration_ms=total_ms,
            scenes=tuple(resolved),
            resolved_duration_ms=total_ms,
            maximum_allowed_duration_ms=total_ms,
            accepted=True,
        ),
    )


def _expansion(scene_durations_ms: tuple[int, ...]) -> PostTtsShotExpansion:
    scene_plan, duration_resolution = _inputs(scene_durations_ms)
    return build_post_tts_shot_expansion(
        job_id=JOB_ID,
        source_scene_plan_artifact_id=SCENE_ARTIFACT_ID,
        source_scene_plan_sha256="b" * 64,
        source_duration_artifact_id=DURATION_ARTIFACT_ID,
        source_duration_sha256="c" * 64,
        scene_plan=scene_plan,
        duration_resolution=duration_resolution,
        supported_provider_durations_seconds=(4, 6, 8),
        visual_strategy_name=VisualStrategyName.IMAGE_ONLY,
    )


def _counts_by_scene(expansion: PostTtsShotExpansion) -> tuple[int, ...]:
    return tuple(len(scene.shots) for scene in expansion.expanded_scene_plan.scenes)


def _image_strategy(scene_durations_ms: tuple[int, ...]):
    expansion = _expansion(scene_durations_ms)
    strategy = build_hybrid_visual_strategy_plan(
        job_id=JOB_ID,
        source_shot_expansion_artifact_id=EXPANSION_ARTIFACT_ID,
        source_shot_expansion_sha256="d" * 64,
        source_shot_expansion_fingerprint=expansion.plan_fingerprint,
        shots=expansion.allocations,
        strategy_name=VisualStrategyName.IMAGE_ONLY,
    )
    return expansion, strategy


def _image_authorization(
    *,
    maximum_requests: int,
    maximum_cost: str,
) -> HybridVisualBudgetAuthorization:
    return HybridVisualBudgetAuthorization(
        estimated_image_cost_per_request_usd=Decimal("0.04"),
        video_price_per_second_usd=Decimal("0.03"),
        maximum_image_requests=maximum_requests,
        maximum_video_requests=0,
        maximum_authorized_image_cost_usd=Decimal(maximum_cost),
        maximum_authorized_video_cost_per_request_usd=Decimal("0"),
        maximum_authorized_video_cost_usd=Decimal("0"),
        maximum_authorized_total_visual_cost_usd=Decimal(maximum_cost),
    )


def test_density_policy_has_explicit_deterministic_bounds() -> None:
    policy = VisualShotDensityPolicy()

    assert policy.desired_shot_count(total_duration_ms=1_000, scene_count=1) == 1
    assert policy.desired_shot_count(total_duration_ms=1_000_000, scene_count=1) == 16
    with pytest.raises(ValueError, match="narrative scene count"):
        policy.desired_shot_count(total_duration_ms=60_000, scene_count=17)


@pytest.mark.parametrize(
    ("scene_durations_ms", "expected_shots"),
    (
        ((7_500, 7_500), 5),
        ((12_500, 12_500), 8),
        ((25_000, 25_000), 15),
    ),
)
def test_image_only_shot_count_scales_with_duration(
    scene_durations_ms: tuple[int, ...],
    expected_shots: int,
) -> None:
    expansion = _expansion(scene_durations_ms)

    assert len(expansion.allocations) == expected_shots
    assert all(
        allocation.visual_mode is VisualMode.GENERATED_IMAGE
        and allocation.provider_duration_seconds is None
        for allocation in expansion.allocations
    )


def test_equal_and_unequal_scenes_receive_proportional_deterministic_shots() -> None:
    equal = _expansion((12_500, 12_500))
    unequal = _expansion((5_000, 20_000))

    assert _counts_by_scene(equal) == (4, 4)
    assert _counts_by_scene(unequal) == (2, 6)


def test_shot_durations_cover_each_scene_exactly_and_preserve_order() -> None:
    expansion = _expansion((5_001, 19_999))

    assert tuple(
        sum(allocation.usable_duration_ms for allocation in expansion.allocations if allocation.scene_id == scene.scene_id)
        for scene in expansion.expanded_scene_plan.scenes
    ) == (5_001, 19_999)
    assert sum(item.usable_duration_ms for item in expansion.allocations) == 25_000
    assert tuple(item.scene_id for item in expansion.allocations) == tuple(
        scene.scene_id
        for scene in expansion.expanded_scene_plan.scenes
        for _ in scene.shots
    )
    assert tuple(item.shot_sequence_index for item in expansion.allocations) == (
        0,
        1,
        0,
        1,
        2,
        3,
        4,
        5,
    )


def test_same_input_has_identical_expansion_and_fingerprint() -> None:
    first = _expansion((12_500, 12_500))
    second = _expansion((12_500, 12_500))

    assert first == second
    assert first.plan_fingerprint == second.plan_fingerprint
    assert PostTtsShotExpansion.model_validate_json(first.model_dump_json()) == first


def test_eight_image_shots_are_authorized_by_explicit_count_and_cost() -> None:
    _, strategy = _image_strategy((12_500, 12_500))
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_image_authorization(
            maximum_requests=8,
            maximum_cost="0.32",
        ),
    )

    assert strategy.summary.visual_shot_count == 8
    assert strategy.summary.generated_image_shots == 8
    assert strategy.summary.generated_video_shots == 0
    assert budget.image_requests == 8
    assert budget.video_requests == 0
    assert budget.estimated_image_cost_usd == Decimal("0.32")
    assert authorize_aggregate_visual_budget(budget) == budget


@pytest.mark.parametrize(
    ("maximum_requests", "maximum_cost"),
    (
        (7, "0.32"),
        (8, "0.31"),
    ),
)
def test_eight_image_shots_fail_closed_on_count_or_cost(
    maximum_requests: int,
    maximum_cost: str,
) -> None:
    _, strategy = _image_strategy((12_500, 12_500))
    budget = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_image_authorization(
            maximum_requests=maximum_requests,
            maximum_cost=maximum_cost,
        ),
    )

    assert budget.image_requests == 8
    assert budget.budget_pass is False
    with pytest.raises(AggregateVisualBudgetError):
        authorize_aggregate_visual_budget(budget)


def test_policy_maximum_sixteen_images_is_fully_bounded() -> None:
    expansion, strategy = _image_strategy((100_000,))
    authorized = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_image_authorization(
            maximum_requests=16,
            maximum_cost="0.64",
        ),
    )
    cost_rejected = build_aggregate_visual_budget_plan(
        strategy_plan=strategy,
        authorization=_image_authorization(
            maximum_requests=16,
            maximum_cost="0.63",
        ),
    )

    assert len(expansion.allocations) == 16
    assert authorized.image_requests == 16
    assert authorized.estimated_image_cost_usd == Decimal("0.64")
    assert authorize_aggregate_visual_budget(authorized) == authorized
    assert cost_rejected.budget_pass is False
    with pytest.raises(AggregateVisualBudgetError):
        authorize_aggregate_visual_budget(cost_rejected)


@pytest.mark.parametrize(
    "visual_strategy_name",
    (
        VisualStrategyName.FULL_VIDEO,
        VisualStrategyName.HYBRID_BALANCED,
        VisualStrategyName.HYBRID_ECONOMY,
    ),
)
def test_video_capable_strategies_keep_provider_duration_driven_legacy_expansion(
    visual_strategy_name: VisualStrategyName,
) -> None:
    scene_plan, duration_resolution = _inputs((9_000,))
    expansion = build_post_tts_shot_expansion(
        job_id=JOB_ID,
        source_scene_plan_artifact_id=SCENE_ARTIFACT_ID,
        source_scene_plan_sha256="b" * 64,
        source_duration_artifact_id=DURATION_ARTIFACT_ID,
        source_duration_sha256="c" * 64,
        scene_plan=scene_plan,
        duration_resolution=duration_resolution,
        supported_provider_durations_seconds=(4, 6, 8),
        visual_strategy_name=visual_strategy_name,
    )

    assert tuple(item.provider_duration_seconds for item in expansion.allocations) == (6, 4)
    assert tuple(item.usable_duration_ms for item in expansion.allocations) == (6_000, 3_000)
    assert all(item.visual_mode is VisualMode.GENERATED_VIDEO for item in expansion.allocations)
