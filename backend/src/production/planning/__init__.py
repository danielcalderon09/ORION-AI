"""Provider-neutral planning contracts for the PLANNING stage."""

from backend.src.production.domain.visual_strategy import (
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
)
from backend.src.production.planning.models import (
    PlanningJobConfiguration,
    ProductionPlan,
    ProductionScenePlan,
)
from backend.src.production.planning.provider_budget_planner import (
    AudioFirstNarrativePlan,
    BoundVisualShot,
    EditorialDurationPlan,
    EditorialSceneAllocation,
    ResolvedNarrativeScene,
    SceneProviderPurchasePlan,
    VideoGenerationMode,
    VideoProviderPurchasePlan,
    VideoPurchaseBudgetError,
    VisualClipPurchase,
    VisualShotAllocation,
    VisualShotFunction,
    allocate_editorial_duration_plan,
    allocate_visual_shots,
    authorize_video_purchase_plan,
    build_bound_video_purchase_plan,
    build_video_purchase_plan,
    cover_duration_with_provider_clips,
    propose_scene_count,
    resolve_editorial_audio_first,
)
from backend.src.production.planning.serialization import serialize_production_plan
from backend.src.production.planning.visual_strategy import LegacyFullVideoStrategy

__all__ = [
    "PlanningJobConfiguration",
    "ProductionPlan",
    "ProductionScenePlan",
    "serialize_production_plan",
    "AudioFirstNarrativePlan",
    "BoundVisualShot",
    "EditorialDurationPlan",
    "EditorialSceneAllocation",
    "ResolvedNarrativeScene",
    "SceneProviderPurchasePlan",
    "VideoGenerationMode",
    "VideoProviderPurchasePlan",
    "VideoPurchaseBudgetError",
    "VisualClipPurchase",
    "VisualShotAllocation",
    "VisualShotFunction",
    "allocate_editorial_duration_plan",
    "allocate_visual_shots",
    "authorize_video_purchase_plan",
    "build_video_purchase_plan",
    "build_bound_video_purchase_plan",
    "cover_duration_with_provider_clips",
    "propose_scene_count",
    "resolve_editorial_audio_first",
    "LegacyFullVideoStrategy",
    "VisualGenerationPriority",
    "VisualImportance",
    "VisualMode",
    "VisualMotionMode",
]
