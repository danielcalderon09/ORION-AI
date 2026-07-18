from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class MusicHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.PREPARING_MUSIC})
    artifact_type = ArtifactType.MUSIC
    mime_type = "audio/wav"
    extension = "wav"
