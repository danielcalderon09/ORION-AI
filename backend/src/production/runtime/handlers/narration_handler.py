from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class NarrationHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.GENERATING_NARRATION})
    artifact_type = ArtifactType.NARRATION
    mime_type = "audio/wav"
    extension = "wav"
