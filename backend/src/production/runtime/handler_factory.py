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
    ScriptHandler,
    ScriptingHandler,
    SubtitleHandler,
    TimelineHandler,
    ValidationHandler,
)
from backend.src.production.runtime.handlers import (
    ScenePlanningHandler as SimulatedScenePlanningHandler,
)
from backend.src.production.runtime.job_dispatcher import StageHandlerRegistry
from backend.src.production.scene_planning.handler import ScenePlanningHandler


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
            SimulatedScenePlanningHandler(clock=clock, uuid_factory=uuid_factory),
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
    scripting_handler: ScriptingHandler | None = None,
    scene_planning_handler: ScenePlanningHandler | None = None,
    clock: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
) -> StageHandlerRegistry:
    """Inject durable early-stage handlers; later media stages remain simulated."""

    return StageHandlerRegistry(
        (
            planning_handler,
            scripting_handler or ScriptHandler(clock=clock, uuid_factory=uuid_factory),
            scene_planning_handler
            or SimulatedScenePlanningHandler(clock=clock, uuid_factory=uuid_factory),
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
