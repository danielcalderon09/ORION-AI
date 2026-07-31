"""Simulated local handlers for every executable production stage."""

from backend.src.production.runtime.handlers.asset_handler import AssetHandler
from backend.src.production.runtime.handlers.base import StageHandler
from backend.src.production.runtime.handlers.clip_handoff_handler import ClipHandoffHandler
from backend.src.production.runtime.handlers.music_handler import MusicHandler
from backend.src.production.runtime.handlers.narration_handler import NarrationHandler
from backend.src.production.runtime.handlers.planning_handler import PlanningHandler
from backend.src.production.runtime.handlers.scene_planning_handler import ScenePlanningHandler
from backend.src.production.runtime.handlers.script_handler import ScriptHandler, ScriptingHandler
from backend.src.production.runtime.handlers.subtitle_handler import (
    DurableSubtitleHandler,
    SubtitleHandler,
)
from backend.src.production.runtime.handlers.timeline_handler import TimelineHandler
from backend.src.production.runtime.handlers.validation_handler import ValidationHandler
from backend.src.production.runtime.handlers.visual_asset_planning_handler import (
    VisualAssetPlanningHandler,
)

__all__ = [
    "AssetHandler",
    "ClipHandoffHandler",
    "MusicHandler",
    "NarrationHandler",
    "PlanningHandler",
    "ScenePlanningHandler",
    "ScriptHandler",
    "ScriptingHandler",
    "StageHandler",
    "SubtitleHandler",
    "DurableSubtitleHandler",
    "TimelineHandler",
    "ValidationHandler",
    "VisualAssetPlanningHandler",
]
