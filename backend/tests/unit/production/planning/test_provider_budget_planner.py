"""Offline tests for editorial and provider purchase planning."""

from decimal import Decimal
from itertools import product
from math import ceil

import pytest

from backend.src.production.domain.duration_resolution import DurationResolutionPolicy
from backend.src.production.planning import (
    VideoPurchaseBudgetError,
    allocate_editorial_duration_plan,
    allocate_visual_shots,
    authorize_video_purchase_plan,
    build_video_purchase_plan,
    cover_duration_with_provider_clips,
    propose_scene_count,
    resolve_editorial_audio_first,
)
from backend.src.production.planning.provider_budget_planner import AudioFirstNarrativePlan
from backend.src.production.scripting.models import adaptive_narrative_roles


def test_explicit_scene_count_is_respected_and_roles_are_adaptive() -> None:
    assert propose_scene_count(45_000, explicit_scene_count=5) == 5
    plan = allocate_editorial_duration_plan(
        requested_duration_ms=45_000,
        scene_count=5,
        narrative_roles=adaptive_narrative_roles(5),
    )
    assert len(plan.scenes) == 5
    assert sum(scene.editorial_target_ms for scene in plan.scenes) == 45_000
    assert plan.scenes[0].narrative_role.value == "hook"
    assert plan.scenes[-1].narrative_role.value == "payoff"
    assert len({scene.editorial_target_ms for scene in plan.scenes}) > 1


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    ((8_000, 2), (25_000, 4), (45_000, 6), (120_000, 12)),
)
def test_adaptive_scene_count(duration_ms: int, expected: int) -> None:
    assert propose_scene_count(duration_ms) == expected


@pytest.mark.parametrize(
    ("required_ms", "expected"),
    ((9_000, (6, 4)), (14_000, (8, 6)), (16_000, (8, 8))),
)
def test_provider_clip_coverage_minimizes_purchased_seconds(
    required_ms: int, expected: tuple[int, ...]
) -> None:
    result = cover_duration_with_provider_clips(required_ms, (4, 6, 8))
    assert result == expected
    assert sum(result) * 1_000 >= required_ms


def test_nine_second_scene_allocates_two_distinct_visual_shots() -> None:
    resolved = _resolved_plan(target_ms=9_000, narration_ms=(9_000,))

    shots = allocate_visual_shots(
        resolved.scenes[0],
        supported_durations_seconds=(4, 6, 8),
    )

    assert tuple(shot.provider_duration_seconds for shot in shots) == (6, 4)
    assert tuple(shot.usable_duration_ms for shot in shots) == (6_000, 3_000)
    assert len({shot.shot_id for shot in shots}) == 2
    assert len({shot.visual_asset_id for shot in shots}) == 2
    assert len({shot.intent_key for shot in shots}) == 2


def _resolved_plan(
    *,
    target_ms: int = 8_000,
    narration_ms: tuple[int, ...] = (4_250, 5_000),
) -> AudioFirstNarrativePlan:
    roles = adaptive_narrative_roles(len(narration_ms))
    editorial = allocate_editorial_duration_plan(
        requested_duration_ms=target_ms,
        scene_count=len(narration_ms),
        narrative_roles=roles,
    )
    return resolve_editorial_audio_first(
        editorial,
        narration_ms,
        DurationResolutionPolicy(),
    )


def test_audio_first_resolution_precedes_provider_purchase_planning() -> None:
    resolved = _resolved_plan()
    assert resolved.resolved_duration_ms == 9_250
    plan = build_video_purchase_plan(
        resolved_plan=resolved,
        provider="openrouter",
        model="google/veo-3.1-lite",
        supported_durations_seconds=(4, 6, 8),
        price_per_second_usd=Decimal("0.03"),
        max_requests_per_job=2,
        maximum_authorized_cost_per_request_usd=Decimal("0.20"),
        maximum_authorized_cost_usd=Decimal("0.40"),
    )
    assert [tuple(clip.provider_duration_seconds for clip in scene.clips) for scene in plan.scenes] == [
        (6,),
        (6,),
    ]
    assert plan.estimated_cost_usd == Decimal("0.36")


def test_budget_rejection_happens_before_submission_and_acceptance_is_explicit() -> None:
    resolved = _resolved_plan()
    rejected = build_video_purchase_plan(
        resolved_plan=resolved,
        provider="openrouter",
        model="google/veo-3.1-lite",
        supported_durations_seconds=(4, 6, 8),
        price_per_second_usd=Decimal("0.03"),
        max_requests_per_job=2,
        maximum_authorized_cost_per_request_usd=Decimal("0.20"),
        maximum_authorized_cost_usd=Decimal("0.25"),
    )
    assert rejected.accepted is False
    with pytest.raises(VideoPurchaseBudgetError):
        authorize_video_purchase_plan(rejected)

    accepted = rejected.model_copy(update={"maximum_authorized_cost_usd": Decimal("0.40"), "accepted": True})
    assert authorize_video_purchase_plan(accepted).estimated_cost_usd == Decimal("0.36")


def test_forty_five_second_reference_separates_narrative_scenes_from_visual_clips() -> None:
    roles = adaptive_narrative_roles(5)
    editorial = allocate_editorial_duration_plan(
        requested_duration_ms=45_000,
        scene_count=5,
        narrative_roles=roles,
    )
    resolved = resolve_editorial_audio_first(
        editorial,
        (7_000, 8_500, 9_000, 10_000, 11_000),
        DurationResolutionPolicy(),
    )
    plan = build_video_purchase_plan(
        resolved_plan=resolved,
        provider="openrouter",
        model="google/veo-3.1-lite",
        supported_durations_seconds=(4, 6, 8),
        price_per_second_usd=Decimal("0.03"),
        max_requests_per_job=20,
        maximum_authorized_cost_per_request_usd=Decimal("0.25"),
        maximum_authorized_cost_usd=Decimal("2.00"),
    )
    assert len(plan.scenes) == 5
    assert plan.total_clip_count > len(plan.scenes)
    assert plan.total_purchased_seconds == 54
    assert plan.estimated_cost_usd == Decimal("1.62")
    assert all(clip.adaptation in {"none", "trim"} for scene in plan.scenes for clip in scene.clips)


def test_purchase_plan_is_deterministic_and_never_uses_loops() -> None:
    first = build_video_purchase_plan(
        resolved_plan=_resolved_plan(),
        provider="openrouter",
        model="google/veo-3.1-lite",
        supported_durations_seconds=(8, 4, 6),
        price_per_second_usd=Decimal("0.03"),
        max_requests_per_job=2,
        maximum_authorized_cost_per_request_usd=Decimal("0.20"),
        maximum_authorized_cost_usd=Decimal("0.40"),
    )
    second = build_video_purchase_plan(
        resolved_plan=_resolved_plan(),
        provider="openrouter",
        model="google/veo-3.1-lite",
        supported_durations_seconds=(8, 4, 6),
        price_per_second_usd=Decimal("0.03"),
        max_requests_per_job=2,
        maximum_authorized_cost_per_request_usd=Decimal("0.20"),
        maximum_authorized_cost_usd=Decimal("0.40"),
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert all(clip.adaptation != "loop" for scene in first.scenes for clip in scene.clips)


def test_historical_audio_first_contract_remains_readable() -> None:
    resolved = _resolved_plan(narration_ms=(3_500, 3_800))
    assert resolved.resolved_duration_ms == 8_000
    assert resolved.accepted is True


def test_per_request_cost_can_reject_an_aggregate_authorized_plan() -> None:
    plan = build_video_purchase_plan(
        resolved_plan=_resolved_plan(),
        provider="openrouter",
        model="google/veo-3.1-lite",
        supported_durations_seconds=(4, 6, 8),
        price_per_second_usd=Decimal("0.03"),
        max_requests_per_job=2,
        maximum_authorized_cost_per_request_usd=Decimal("0.17"),
        maximum_authorized_cost_usd=Decimal("0.40"),
    )
    assert plan.estimated_cost_usd == Decimal("0.36")
    assert plan.accepted is False
    with pytest.raises(VideoPurchaseBudgetError):
        authorize_video_purchase_plan(plan)


def test_bounded_exhaustive_provider_coverage_is_optimal_and_deterministic() -> None:
    supported = (4, 6, 8)
    for required_seconds in range(9, 18):
        actual = cover_duration_with_provider_clips(required_seconds * 1_000, supported)
        candidates = {
            tuple(sorted(candidate, reverse=True))
            for count in range(1, ceil(required_seconds / min(supported)) + 2)
            for candidate in product(supported, repeat=count)
            if sum(candidate) >= required_seconds
        }
        expected = min(
            candidates,
            key=lambda item: (sum(item), len(item), tuple(-value for value in item)),
        )
        assert actual == expected
        assert sum(actual) >= required_seconds
        assert actual == cover_duration_with_provider_clips(
            required_seconds * 1_000,
            tuple(reversed(supported)),
        )
