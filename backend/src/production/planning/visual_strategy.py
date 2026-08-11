"""Pure visual strategy planners with no provider or runtime side effects."""

from dataclasses import dataclass

from backend.src.production.domain.visual_strategy import (
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
)
from backend.src.production.planning.provider_budget_planner import (
    VisualShotAllocation,
)


@dataclass(frozen=True, slots=True)
class LegacyFullVideoStrategy:
    """Preserve the existing one-generated-video-per-shot behavior exactly."""

    name: str = "legacy_full_video_v1"

    def apply(
        self,
        shots: tuple[VisualShotAllocation, ...],
    ) -> tuple[VisualShotAllocation, ...]:
        planned: list[VisualShotAllocation] = []
        for shot in shots:
            if shot.provider_duration_seconds is None:
                raise ValueError("legacy full-video strategy requires provider duration")
            payload = shot.model_dump(mode="python")
            payload.update(
                {
                    "visual_mode": VisualMode.GENERATED_VIDEO,
                    "motion_mode": VisualMotionMode.STATIC,
                    "source_asset_id": None,
                    "importance": VisualImportance.MEDIUM,
                    "generation_priority": VisualGenerationPriority.NORMAL,
                }
            )
            planned.append(VisualShotAllocation.model_validate(payload))
        return tuple(planned)


__all__ = ["LegacyFullVideoStrategy"]
