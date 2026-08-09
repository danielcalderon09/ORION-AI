"""Deterministic narration bounds for controlled OpenRouter scripting."""

from __future__ import annotations

import math

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.scripting.models import ProductionScript


class ScriptingDurationAssessment(ContractModel):
    target_duration_seconds: float = Field(gt=0, le=60)
    scene_count: int = Field(ge=1, le=50)
    narration_word_count: int = Field(ge=1)
    minimum_word_count: int = Field(ge=1)
    maximum_word_count: int = Field(ge=1)
    reading_speed_words_per_minute: int = Field(ge=80, le=240)


def narration_word_count_bounds(
    *,
    target_duration_seconds: float,
    scene_count: int,
    reading_speed_words_per_minute: int,
) -> tuple[int, int]:
    """Return the exact total-narration bounds enforced for a script request."""

    if not 4 <= target_duration_seconds <= 60:
        raise ValueError("OpenRouter scripting supports local durations from 4 to 60 seconds")
    if scene_count < 1:
        raise ValueError("scripting requires at least one scene")
    if not 80 <= reading_speed_words_per_minute <= 240:
        raise ValueError("scripting reading speed is outside supported bounds")
    expected = target_duration_seconds * reading_speed_words_per_minute / 60
    minimum = max(scene_count * 2, math.floor(expected * 0.20))
    maximum = max(minimum, math.ceil(expected * 1.60))
    return minimum, maximum


def validate_openrouter_duration_policy(
    script: ProductionScript,
    *,
    reading_speed_words_per_minute: int,
) -> ScriptingDurationAssessment:
    """Reject clearly unusable narration without pretending to predict exact speech timing."""

    counts = tuple(len(scene.narration.split()) for scene in script.scenes)
    if any(count < 2 for count in counts):
        raise ValueError("every script scene requires meaningful narration")
    minimum, maximum = narration_word_count_bounds(
        target_duration_seconds=script.target_duration_seconds,
        scene_count=len(script.scenes),
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )
    total = sum(counts)
    if total < minimum:
        raise ValueError("script narration is insufficient for the requested duration")
    if total > maximum:
        raise ValueError("script narration exceeds the requested duration policy")
    return ScriptingDurationAssessment(
        target_duration_seconds=script.target_duration_seconds,
        scene_count=len(script.scenes),
        narration_word_count=total,
        minimum_word_count=minimum,
        maximum_word_count=maximum,
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )


__all__ = [
    "ScriptingDurationAssessment",
    "narration_word_count_bounds",
    "validate_openrouter_duration_policy",
]
