from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class ScenePlanningHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.SCENE_PLANNING})
    artifact_type = ArtifactType.MANIFEST
    mime_type = "application/json"
    extension = "json"
