"""Deterministic narration bounds for controlled OpenRouter scripting."""

from __future__ import annotations

import math
import re
import unicodedata
from decimal import ROUND_FLOOR, Decimal

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.scripting.models import (
    NarrativeRole,
    ProductionScript,
    adaptive_narrative_roles,
)


class ScriptingDurationAssessment(ContractModel):
    target_duration_seconds: float = Field(gt=0, le=60)
    scene_count: int = Field(ge=1, le=50)
    narration_word_count: int = Field(ge=1)
    minimum_word_count: int = Field(ge=1)
    maximum_word_count: int = Field(ge=1)
    reading_speed_words_per_minute: int = Field(ge=80, le=240)
    normalized_narration: str = Field(min_length=1, repr=False)
    punctuation_count: int = Field(ge=0)
    estimated_duration_ms: int = Field(gt=0)
    target_duration_ms: int = Field(gt=0)
    accepted: bool


_WORDS = re.compile(r"\b[\wÀ-ÿ]+\b", re.UNICODE)
_PUNCTUATION = re.compile(r"[,;:.!?]")
_WHITESPACE = re.compile(r"\s+")
PUNCTUATION_ALLOWANCE_MS = 120
PROMPT_WORDS_PER_PUNCTUATION = 5


_ROLE_WORD_WEIGHTS: dict[NarrativeRole, float] = {
    NarrativeRole.HOOK: 0.85,
    NarrativeRole.SETUP: 1.0,
    NarrativeRole.DEVELOPMENT: 1.15,
    NarrativeRole.ESCALATION: 1.1,
    NarrativeRole.REVEAL: 1.05,
    NarrativeRole.PAYOFF: 0.9,
    NarrativeRole.CONCLUSION: 0.9,
}


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
    maximum = max(minimum, math.floor(expected))
    return minimum, maximum


def narration_scene_word_budgets(
    *,
    target_duration_seconds: float,
    scene_count: int,
    reading_speed_words_per_minute: int,
) -> tuple[int, ...]:
    """Allocate the conservative prompt maximum across narrative roles."""

    _, maximum = narration_prompt_word_count_bounds(
        target_duration_seconds=target_duration_seconds,
        scene_count=scene_count,
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )
    return allocate_narration_scene_word_budgets(
        scene_count=scene_count,
        maximum_total_words=maximum,
    )


def allocate_narration_scene_word_budgets(
    *,
    scene_count: int,
    maximum_total_words: int,
) -> tuple[int, ...]:
    """Allocate an explicit global word budget deterministically across roles."""

    if scene_count < 1:
        raise ValueError("scripting requires at least one scene")
    roles = adaptive_narrative_roles(scene_count)
    minimum_per_scene = 2
    if maximum_total_words < minimum_per_scene * scene_count:
        raise ValueError("narration word budget cannot represent every scene")
    remaining = maximum_total_words - (minimum_per_scene * scene_count)
    weights = tuple(_ROLE_WORD_WEIGHTS[role] for role in roles)
    total_weight = sum(weights)
    raw_extras = tuple(remaining * weight / total_weight for weight in weights)
    extras = [int(value) for value in raw_extras]
    remainder = remaining - sum(extras)
    order = sorted(
        range(scene_count),
        key=lambda index: (-(raw_extras[index] - extras[index]), index),
    )
    for index in order[:remainder]:
        extras[index] += 1
    return tuple(minimum_per_scene + extra for extra in extras)


def narration_prompt_word_count_bounds(
    *,
    target_duration_seconds: float,
    scene_count: int,
    reading_speed_words_per_minute: int,
) -> tuple[int, int]:
    """Return prompt guidance with deterministic punctuation headroom."""

    minimum, maximum = narration_word_count_bounds(
        target_duration_seconds=target_duration_seconds,
        scene_count=scene_count,
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )
    target_duration_ms = round(target_duration_seconds * 1_000)
    for candidate in range(maximum, minimum - 1, -1):
        reserved_punctuation = max(
            scene_count,
            math.ceil(candidate / PROMPT_WORDS_PER_PUNCTUATION),
        )
        estimated_duration_ms = int(
            (Decimal(candidate * 60_000) / Decimal(reading_speed_words_per_minute))
            .to_integral_value(rounding=ROUND_FLOOR)
        ) + reserved_punctuation * PUNCTUATION_ALLOWANCE_MS
        if estimated_duration_ms <= target_duration_ms:
            return minimum, candidate
    return minimum, minimum


def narration_retry_word_budget(
    assessment: ScriptingDurationAssessment,
    *,
    original_maximum_words: int,
) -> int:
    """Derive stricter retry guidance from the rejected duration assessment."""

    if assessment.accepted:
        return original_maximum_words
    proportional_maximum = math.floor(
        assessment.narration_word_count
        * assessment.target_duration_ms
        / assessment.estimated_duration_ms
    )
    stricter_maximum = max(
        assessment.minimum_word_count,
        original_maximum_words - 1,
    )
    return max(
        assessment.minimum_word_count,
        min(stricter_maximum, proportional_maximum),
    )


def validate_openrouter_duration_policy(
    script: ProductionScript,
    *,
    reading_speed_words_per_minute: int,
) -> ScriptingDurationAssessment:
    """Reject clearly unusable narration without pretending to predict exact speech timing."""

    assessment = assess_narration_duration(
        narrations=tuple(scene.narration for scene in script.scenes),
        target_duration_seconds=script.target_duration_seconds,
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )
    counts = tuple(len(_WORDS.findall(scene.narration)) for scene in script.scenes)
    if any(count < 2 for count in counts):
        raise ValueError("every script scene requires meaningful narration")
    minimum, maximum = narration_word_count_bounds(
        target_duration_seconds=script.target_duration_seconds,
        scene_count=len(script.scenes),
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )
    total = assessment.narration_word_count
    if total < minimum:
        raise ValueError("script narration is insufficient for the requested duration")
    if total > maximum:
        raise ValueError("script narration exceeds the requested duration policy")
    if not assessment.accepted:
        raise ValueError("script narration exceeds the requested duration policy")
    return assessment.model_copy(
        update={"minimum_word_count": minimum, "maximum_word_count": maximum}
    )


def assess_narration_duration(
    *,
    narrations: tuple[str, ...],
    target_duration_seconds: float,
    reading_speed_words_per_minute: int,
) -> ScriptingDurationAssessment:
    """Estimate combined narration duration without audio or provider dependencies."""

    if not 4 <= target_duration_seconds <= 60:
        raise ValueError("OpenRouter scripting supports local durations from 4 to 60 seconds")
    if not narrations:
        raise ValueError("scripting requires at least one scene")
    if not 80 <= reading_speed_words_per_minute <= 240:
        raise ValueError("scripting reading speed is outside supported bounds")
    normalized = " ".join(
        _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()
        for value in narrations
    ).strip()
    if not normalized:
        raise ValueError("script narration is empty")
    word_count = len(_WORDS.findall(normalized))
    punctuation_count = len(_PUNCTUATION.findall(normalized))
    target_duration_ms = round(target_duration_seconds * 1_000)
    estimated_duration_ms = int(
        (Decimal(word_count * 60_000) / Decimal(reading_speed_words_per_minute))
        .to_integral_value(rounding=ROUND_FLOOR)
    ) + punctuation_count * PUNCTUATION_ALLOWANCE_MS
    minimum, maximum = narration_word_count_bounds(
        target_duration_seconds=target_duration_seconds,
        scene_count=len(narrations),
        reading_speed_words_per_minute=reading_speed_words_per_minute,
    )
    return ScriptingDurationAssessment(
        target_duration_seconds=target_duration_seconds,
        scene_count=len(narrations),
        narration_word_count=word_count,
        minimum_word_count=minimum,
        maximum_word_count=maximum,
        reading_speed_words_per_minute=reading_speed_words_per_minute,
        normalized_narration=normalized,
        punctuation_count=punctuation_count,
        estimated_duration_ms=estimated_duration_ms,
        target_duration_ms=target_duration_ms,
        accepted=estimated_duration_ms <= target_duration_ms,
    )


__all__ = [
    "ScriptingDurationAssessment",
    "allocate_narration_scene_word_budgets",
    "assess_narration_duration",
    "narration_prompt_word_count_bounds",
    "narration_retry_word_budget",
    "narration_scene_word_budgets",
    "narration_word_count_bounds",
    "validate_openrouter_duration_policy",
]
