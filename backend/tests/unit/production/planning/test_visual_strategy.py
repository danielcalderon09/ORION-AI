"""Offline contracts for provider-neutral visual strategy planning."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.src.production.domain.duration_resolution import DurationResolutionPolicy
from backend.src.production.planning import (
    LegacyFullVideoStrategy,
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
    VisualShotAllocation,
    allocate_editorial_duration_plan,
    allocate_visual_shots,
    resolve_editorial_audio_first,
)
from backend.src.production.planning.provider_budget_planner import VisualShotFunction
from backend.src.production.scripting.models import NarrativeRole, adaptive_narrative_roles


def _shot(**updates: object) -> VisualShotAllocation:
    payload: dict[str, object] = {
        "scene_id": "scene-001",
        "shot_id": "scene-001-shot-001",
        "shot_sequence_index": 0,
        "visual_asset_id": "asset-s001-q001-v001",
        "narrative_role": NarrativeRole.HOOK,
        "visual_function": VisualShotFunction.PRIMARY,
        "intent_key": "scene-001:hook:primary:1-of-1",
        "usable_duration_ms": 5_200,
        "provider_duration_seconds": 6,
    }
    payload.update(updates)
    return VisualShotAllocation.model_validate(payload)


def test_visual_strategy_enums_serialize_to_stable_provider_neutral_values() -> None:
    shot = _shot(
        visual_mode=VisualMode.GENERATED_IMAGE,
        motion_mode=VisualMotionMode.PAN_AND_ZOOM,
        provider_duration_seconds=None,
        importance=VisualImportance.HERO,
        generation_priority=VisualGenerationPriority.REQUIRED,
    )

    assert shot.model_dump(mode="json") == {
        "scene_id": "scene-001",
        "shot_id": "scene-001-shot-001",
        "shot_sequence_index": 0,
        "visual_asset_id": "asset-s001-q001-v001",
        "narrative_role": "hook",
        "visual_function": "primary",
        "intent_key": "scene-001:hook:primary:1-of-1",
        "usable_duration_ms": 5_200,
        "visual_mode": "generated_image",
        "motion_mode": "pan_and_zoom",
        "importance": "hero",
        "generation_priority": "required",
    }


@pytest.mark.parametrize("motion_mode", tuple(VisualMotionMode))
def test_generated_image_accepts_every_declared_local_motion_intent(
    motion_mode: VisualMotionMode,
) -> None:
    shot = _shot(
        visual_mode=VisualMode.GENERATED_IMAGE,
        motion_mode=motion_mode,
        provider_duration_seconds=None,
    )
    assert shot.usable_duration_ms == 5_200
    assert shot.provider_duration_seconds is None


@pytest.mark.parametrize(
    "updates",
    (
        {"provider_duration_seconds": None},
        {"visual_mode": VisualMode.GENERATED_IMAGE, "provider_duration_seconds": 6},
        {
            "visual_mode": VisualMode.GENERATED_IMAGE,
            "provider_duration_seconds": None,
            "source_asset_id": "asset-source-001",
        },
        {"visual_mode": VisualMode.REUSED_IMAGE, "provider_duration_seconds": None},
        {"visual_mode": VisualMode.REUSED_VIDEO, "provider_duration_seconds": None},
        {
            "visual_mode": VisualMode.REUSED_IMAGE,
            "provider_duration_seconds": 4,
            "source_asset_id": "asset-source-001",
        },
        {
            "visual_mode": VisualMode.REUSED_VIDEO,
            "provider_duration_seconds": 4,
            "source_asset_id": "asset-source-001",
        },
        {"motion_mode": VisualMotionMode.ZOOM_IN},
    ),
)
def test_ambiguous_visual_strategy_states_fail_closed(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _shot(**updates)


@pytest.mark.parametrize(
    "visual_mode",
    (VisualMode.REUSED_IMAGE, VisualMode.REUSED_VIDEO),
)
def test_reused_visuals_require_source_and_never_use_purchase_duration(
    visual_mode: VisualMode,
) -> None:
    shot = _shot(
        visual_mode=visual_mode,
        motion_mode=(
            VisualMotionMode.PAN
            if visual_mode is VisualMode.REUSED_IMAGE
            else VisualMotionMode.STATIC
        ),
        source_asset_id="asset-source-001",
        provider_duration_seconds=None,
    )
    assert shot.source_asset_id == "asset-source-001"
    assert shot.provider_duration_seconds is None


def test_legacy_full_video_defaults_preserve_historical_serialized_shape() -> None:
    historical = {
        "scene_id": "scene-001",
        "shot_id": "scene-001-shot-001",
        "shot_sequence_index": 0,
        "visual_asset_id": "asset-s001-q001-v001",
        "narrative_role": "hook",
        "visual_function": "primary",
        "intent_key": "scene-001:hook:primary:1-of-1",
        "provider_duration_seconds": 6,
        "usable_duration_ms": 5_200,
    }

    shot = VisualShotAllocation.model_validate(historical)

    assert shot.visual_mode is VisualMode.GENERATED_VIDEO
    assert shot.motion_mode is VisualMotionMode.STATIC
    assert shot.source_asset_id is None
    assert shot.importance is VisualImportance.MEDIUM
    assert shot.generation_priority is VisualGenerationPriority.NORMAL
    assert shot.model_dump(mode="json") == historical


def test_legacy_full_video_strategy_is_deterministic_and_idempotent() -> None:
    shots = (_shot(),)
    strategy = LegacyFullVideoStrategy()

    first = strategy.apply(shots)
    second = strategy.apply(first)

    assert first == second == shots
    assert first[0].visual_mode is VisualMode.GENERATED_VIDEO
    assert first[0].provider_duration_seconds == 6


def test_five_scene_reference_keeps_more_shots_than_scenes_full_video() -> None:
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
    allocated = tuple(
        shot
        for scene in resolved.scenes
        for shot in allocate_visual_shots(
            scene,
            supported_durations_seconds=(4, 6, 8),
        )
    )
    planned = LegacyFullVideoStrategy().apply(allocated)

    assert len(resolved.scenes) == 5
    assert len(planned) == 10
    assert sum(shot.usable_duration_ms for shot in planned) == 47_917
    assert all(shot.visual_mode is VisualMode.GENERATED_VIDEO for shot in planned)
    assert all(shot.provider_duration_seconds is not None for shot in planned)
    assert Decimal("0.03") * sum(
        shot.provider_duration_seconds or 0 for shot in planned
    ) == Decimal("1.62")
