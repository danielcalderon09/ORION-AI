"""Deterministic ordering of production pipeline stages."""

from backend.src.production.domain.enums import ProductionStage


class UnknownProductionStageError(ValueError):
    """Raised when a stage is not active in the requested pipeline."""


class StageRegistry:
    """Resolve adjacent stages for pipelines with or without clip handoff."""

    PIPELINE: tuple[ProductionStage, ...] = (
        ProductionStage.CREATED,
        ProductionStage.PLANNING,
        ProductionStage.SCRIPTING,
        ProductionStage.SCENE_PLANNING,
        ProductionStage.ACQUIRING_ASSETS,
        ProductionStage.GENERATING_NARRATION,
        ProductionStage.PREPARING_MUSIC,
        ProductionStage.GENERATING_SUBTITLES,
        ProductionStage.BUILDING_TIMELINE,
        ProductionStage.RENDERING_LONG_FORM,
        ProductionStage.VALIDATING_RENDER,
        ProductionStage.HANDING_OFF_TO_CLIPS,
        ProductionStage.WAITING_FOR_CLIPS,
        ProductionStage.COMPLETED,
    )

    @classmethod
    def active_stages(cls, *, generate_clips_after_render: bool) -> tuple[ProductionStage, ...]:
        if generate_clips_after_render:
            return cls.PIPELINE
        skipped = {
            ProductionStage.HANDING_OFF_TO_CLIPS,
            ProductionStage.WAITING_FOR_CLIPS,
        }
        return tuple(stage for stage in cls.PIPELINE if stage not in skipped)

    @classmethod
    def next_stage(
        cls,
        current: ProductionStage,
        *,
        generate_clips_after_render: bool,
    ) -> ProductionStage | None:
        stages = cls.active_stages(generate_clips_after_render=generate_clips_after_render)
        try:
            index = stages.index(current)
        except ValueError as exc:
            raise UnknownProductionStageError(f"stage is not active: {current.value}") from exc
        if index == len(stages) - 1:
            return None
        return stages[index + 1]

    @classmethod
    def previous_stage(
        cls,
        current: ProductionStage,
        *,
        generate_clips_after_render: bool,
    ) -> ProductionStage | None:
        stages = cls.active_stages(generate_clips_after_render=generate_clips_after_render)
        try:
            index = stages.index(current)
        except ValueError as exc:
            raise UnknownProductionStageError(f"stage is not active: {current.value}") from exc
        if index == 0:
            return None
        return stages[index - 1]

    @classmethod
    def is_terminal(cls, stage: ProductionStage) -> bool:
        return stage is ProductionStage.COMPLETED

    @classmethod
    def belongs_to_pipeline(cls, stage: ProductionStage) -> bool:
        return stage in cls.PIPELINE
