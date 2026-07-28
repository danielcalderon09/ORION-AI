"""Composition root for simulated Phase 3 handlers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING
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
from backend.src.production.runtime.handlers import (
    VisualAssetPlanningHandler as SimulatedVisualAssetPlanningHandler,
)
from backend.src.production.runtime.handlers.base import StageHandler
from backend.src.production.runtime.job_dispatcher import StageHandlerRegistry

if TYPE_CHECKING:
    from backend.src.production.image_acquisition.handler import (
        ImageAcquisitionHandler,
    )
    from backend.src.production.scene_planning.handler import ScenePlanningHandler
    from backend.src.production.video_clip_generation.handler import (
        VideoClipGenerationHandler,
    )
    from backend.src.production.visual_asset_planning.handler import (
        VisualAssetPlanningHandler,
    )


def create_simulated_handler_registry(
    *,
    clock: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
    video_clip_generation_handler: StageHandler | None = None,
    speech_generation_handler: StageHandler | None = None,
    audio_design_handler: StageHandler | None = None,
) -> StageHandlerRegistry:
    handlers: list[StageHandler] = [
        PlanningHandler(
            provider=SimulatedPlanningProvider(),
            artifact_writer=InMemoryPlanningArtifactWriter(),
            clock=clock,
            uuid_factory=uuid_factory,
        ),
        ScriptHandler(clock=clock, uuid_factory=uuid_factory),
        SimulatedScenePlanningHandler(clock=clock, uuid_factory=uuid_factory),
        SimulatedVisualAssetPlanningHandler(
            clock=clock,
            uuid_factory=uuid_factory,
        ),
        AssetHandler(clock=clock, uuid_factory=uuid_factory),
    ]
    if video_clip_generation_handler is not None:
        handlers.append(video_clip_generation_handler)
    handlers.extend(
        (
            speech_generation_handler or NarrationHandler(clock=clock, uuid_factory=uuid_factory),
            audio_design_handler or MusicHandler(clock=clock, uuid_factory=uuid_factory),
            SubtitleHandler(clock=clock, uuid_factory=uuid_factory),
            TimelineHandler(clock=clock, uuid_factory=uuid_factory),
            RenderHandler(clock=clock, uuid_factory=uuid_factory),
            ValidationHandler(clock=clock, uuid_factory=uuid_factory),
            ClipHandoffHandler(clock=clock, uuid_factory=uuid_factory),
        )
    )
    return StageHandlerRegistry(handlers)


def create_handler_registry(
    *,
    planning_handler: PlanningHandler,
    scripting_handler: ScriptingHandler | None = None,
    scene_planning_handler: ScenePlanningHandler | None = None,
    visual_asset_planning_handler: VisualAssetPlanningHandler | None = None,
    image_acquisition_handler: ImageAcquisitionHandler | None = None,
    video_clip_generation_handler: VideoClipGenerationHandler | None = None,
    speech_generation_handler: StageHandler | None = None,
    audio_design_handler: StageHandler | None = None,
    clock: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
) -> StageHandlerRegistry:
    """Inject durable early-stage handlers; later media stages remain simulated."""

    handlers: list[StageHandler] = [
        planning_handler,
        scripting_handler or ScriptHandler(clock=clock, uuid_factory=uuid_factory),
        scene_planning_handler
        or SimulatedScenePlanningHandler(clock=clock, uuid_factory=uuid_factory),
        visual_asset_planning_handler
        or SimulatedVisualAssetPlanningHandler(
            clock=clock,
            uuid_factory=uuid_factory,
        ),
        image_acquisition_handler or AssetHandler(clock=clock, uuid_factory=uuid_factory),
    ]
    if video_clip_generation_handler is not None:
        handlers.append(video_clip_generation_handler)
    handlers.extend(
        (
            speech_generation_handler or NarrationHandler(clock=clock, uuid_factory=uuid_factory),
            audio_design_handler or MusicHandler(clock=clock, uuid_factory=uuid_factory),
            SubtitleHandler(clock=clock, uuid_factory=uuid_factory),
            TimelineHandler(clock=clock, uuid_factory=uuid_factory),
            RenderHandler(clock=clock, uuid_factory=uuid_factory),
            ValidationHandler(clock=clock, uuid_factory=uuid_factory),
            ClipHandoffHandler(clock=clock, uuid_factory=uuid_factory),
        )
    )
    return StageHandlerRegistry(handlers)
