from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class TimelineHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.BUILDING_TIMELINE})
    artifact_type = ArtifactType.EDIT_PACKAGE
    mime_type = "application/json"
    extension = "json"
