from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class PlanningHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.PLANNING})
    artifact_type = ArtifactType.MANIFEST
    mime_type = "application/json"
    extension = "json"
