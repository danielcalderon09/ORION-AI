"""Capability-aware provider duration selection for one planned scene clip."""

from __future__ import annotations

import math

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoCapabilityError,
)


def select_provider_duration(
    planned_duration_seconds: float,
    supported_durations: tuple[int, ...],
) -> int:
    """Choose the smallest provider duration that fully covers the scene."""

    if not math.isfinite(planned_duration_seconds) or planned_duration_seconds <= 0:
        raise ValueError("planned video duration must be positive and finite")
    durations = tuple(sorted(set(supported_durations)))
    if not durations or any(duration <= 0 for duration in durations):
        raise OpenRouterVideoCapabilityError(
            "OpenRouter video durations are unavailable",
            diagnostic_phase="capability_contract",
            diagnostic_code="capability_duration_unsupported",
        )
    selected = next(
        (duration for duration in durations if duration >= planned_duration_seconds),
        None,
    )
    if selected is None:
        raise OpenRouterVideoCapabilityError(
            "planned scene exceeds the maximum supported video duration",
            diagnostic_phase="capability_contract",
            diagnostic_code="capability_duration_unsupported",
            diagnostic_metadata={"maximum_supported_duration_seconds": max(durations)},
        )
    return selected


__all__ = ["select_provider_duration"]
