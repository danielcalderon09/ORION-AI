from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class ClipHandoffHandler(SimulatedStageHandler):
    supported_stages = frozenset(
        {ProductionStage.HANDING_OFF_TO_CLIPS, ProductionStage.WAITING_FOR_CLIPS}
    )
    artifact_type = ArtifactType.MANIFEST
    mime_type = "application/json"
    extension = "json"
