"""Composition root for simulated Phase 3 handlers."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from backend.src.production.planning.artifact_writer import (
    InMemoryPlanningArtifactWriter,
)
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.runtime.handlers import (
    AssetHandler,
    ClipHandoffHandler,
    MusicHandler,
    NarrationHandler,
    PlanningHandler,
    RenderHandler,
    ScenePlanningHandler,
    ScriptHandler,
    SubtitleHandler,
    TimelineHandler,
    ValidationHandler,
)
from backend.src.production.runtime.job_dispatcher import StageHandlerRegistry


def create_simulated_handler_registry(
    *, clock: Callable[[], datetime], uuid_factory: Callable[[], UUID]
) -> StageHandlerRegistry:
    return StageHandlerRegistry(
        (
            PlanningHandler(
                provider=SimulatedPlanningProvider(),
                artifact_writer=InMemoryPlanningArtifactWriter(),
                clock=clock,
                uuid_factory=uuid_factory,
            ),
            ScriptHandler(clock=clock, uuid_factory=uuid_factory),
            ScenePlanningHandler(clock=clock, uuid_factory=uuid_factory),
            AssetHandler(clock=clock, uuid_factory=uuid_factory),
            NarrationHandler(clock=clock, uuid_factory=uuid_factory),
            MusicHandler(clock=clock, uuid_factory=uuid_factory),
            SubtitleHandler(clock=clock, uuid_factory=uuid_factory),
            TimelineHandler(clock=clock, uuid_factory=uuid_factory),
            RenderHandler(clock=clock, uuid_factory=uuid_factory),
            ValidationHandler(clock=clock, uuid_factory=uuid_factory),
            ClipHandoffHandler(clock=clock, uuid_factory=uuid_factory),
        )
    )


def create_handler_registry(
    *,
    planning_handler: PlanningHandler,
    clock: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
) -> StageHandlerRegistry:
    """Replace only PLANNING while every other stage remains simulated."""

    return StageHandlerRegistry(
        (
            planning_handler,
            ScriptHandler(clock=clock, uuid_factory=uuid_factory),
            ScenePlanningHandler(clock=clock, uuid_factory=uuid_factory),
            AssetHandler(clock=clock, uuid_factory=uuid_factory),
            NarrationHandler(clock=clock, uuid_factory=uuid_factory),
            MusicHandler(clock=clock, uuid_factory=uuid_factory),
            SubtitleHandler(clock=clock, uuid_factory=uuid_factory),
            TimelineHandler(clock=clock, uuid_factory=uuid_factory),
            RenderHandler(clock=clock, uuid_factory=uuid_factory),
            ValidationHandler(clock=clock, uuid_factory=uuid_factory),
            ClipHandoffHandler(clock=clock, uuid_factory=uuid_factory),
        )
    )
