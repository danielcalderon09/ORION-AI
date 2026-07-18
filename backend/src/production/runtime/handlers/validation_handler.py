from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class ValidationHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.VALIDATING_RENDER})
    artifact_type = ArtifactType.MANIFEST
    mime_type = "application/json"
    extension = "json"
