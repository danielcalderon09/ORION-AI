"""Deterministic multi-scene duration allocation."""

import pytest

from backend.src.production.planning.duration_allocation import (
    SceneDurationInput,
    allocate_scene_durations,
)


def test_allocates_contiguous_narration_weighted_scenes_exactly() -> None:
    result = allocate_scene_durations(
        target_duration_ms=15_000,
        scenes=(
            SceneDurationInput(scene_id="scene-001", scene_number=1, narration_word_count=8),
            SceneDurationInput(scene_id="scene-002", scene_number=2, narration_word_count=10),
            SceneDurationInput(scene_id="scene-003", scene_number=3, narration_word_count=12),
        ),
    )

    assert result[0].planned_start_ms == 0
    assert result[-1].planned_end_ms == 15_000
    assert sum(item.planned_duration_ms for item in result) == 15_000
    assert all(
        before.planned_end_ms == after.planned_start_ms
        for before, after in zip(result, result[1:], strict=False)
    )
    assert [item.planned_duration_ms for item in result] != [5_000, 5_000, 5_000]


def test_rejects_target_below_scene_minimums() -> None:
    scenes = tuple(
        SceneDurationInput(
            scene_id=f"scene-{index:03d}", scene_number=index, narration_word_count=1
        )
        for index in range(1, 4)
    )
    with pytest.raises(ValueError, match="minimum scene"):
        allocate_scene_durations(target_duration_ms=2_999, scenes=scenes)
