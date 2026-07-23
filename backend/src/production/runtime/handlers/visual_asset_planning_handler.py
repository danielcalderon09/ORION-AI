from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class VisualAssetPlanningHandler(SimulatedStageHandler):
    """Legacy in-memory runtime placeholder; Production uses the durable handler."""

    supported_stages = frozenset({ProductionStage.VISUAL_ASSET_PLANNING})
    artifact_type = ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN
    mime_type = "application/json"
    extension = "json"
