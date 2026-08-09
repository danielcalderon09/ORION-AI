"""Pure, deterministic allocation of a target duration across ordered scenes."""

from __future__ import annotations

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel


class SceneDurationInput(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    scene_number: int = Field(ge=1, le=50)
    narration_word_count: int = Field(ge=0, le=10_000)


class PlannedSceneDuration(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    scene_number: int = Field(ge=1, le=50)
    planned_start_ms: int = Field(ge=0)
    planned_end_ms: int = Field(gt=0)
    planned_duration_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def contiguous_interval(self) -> PlannedSceneDuration:
        if self.planned_end_ms - self.planned_start_ms != self.planned_duration_ms:
            raise ValueError("planned scene interval differs from its duration")
        return self


def allocate_scene_durations(
    *,
    target_duration_ms: int,
    scenes: tuple[SceneDurationInput, ...],
    minimum_scene_duration_ms: int = 1_000,
) -> tuple[PlannedSceneDuration, ...]:
    """Allocate exact contiguous milliseconds using narration-weighted largest remainder."""

    if not scenes:
        raise ValueError("duration allocation requires at least one scene")
    if target_duration_ms < 1 or minimum_scene_duration_ms < 1:
        raise ValueError("duration allocation values must be positive")
    if target_duration_ms < len(scenes) * minimum_scene_duration_ms:
        raise ValueError("target duration cannot satisfy the minimum scene duration")
    expected_numbers = tuple(range(1, len(scenes) + 1))
    if tuple(scene.scene_number for scene in scenes) != expected_numbers:
        raise ValueError("duration allocation scenes must be consecutively ordered")
    if tuple(scene.scene_id for scene in scenes) != tuple(
        f"scene-{number:03d}" for number in expected_numbers
    ):
        raise ValueError("duration allocation scene identities are inconsistent")

    distributable = target_duration_ms - len(scenes) * minimum_scene_duration_ms
    weights = tuple(max(1, scene.narration_word_count) for scene in scenes)
    total_weight = sum(weights)
    numerators = tuple(distributable * weight for weight in weights)
    extras = [value // total_weight for value in numerators]
    remainder = distributable - sum(extras)
    order = sorted(
        range(len(scenes)),
        key=lambda index: (-(numerators[index] % total_weight), index),
    )
    for index in order[:remainder]:
        extras[index] += 1

    results: list[PlannedSceneDuration] = []
    start = 0
    for index, (scene, extra) in enumerate(zip(scenes, extras, strict=True)):
        duration = minimum_scene_duration_ms + extra
        end = start + duration
        results.append(
            PlannedSceneDuration(
                scene_id=scene.scene_id,
                sequence_index=index,
                scene_number=scene.scene_number,
                planned_start_ms=start,
                planned_end_ms=end,
                planned_duration_ms=duration,
            )
        )
        start = end
    if start != target_duration_ms:
        raise RuntimeError("duration allocation did not consume the exact target")
    return tuple(results)


__all__ = ["PlannedSceneDuration", "SceneDurationInput", "allocate_scene_durations"]
