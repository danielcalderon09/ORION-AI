from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class SubtitleHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.GENERATING_SUBTITLES})
    artifact_type = ArtifactType.SUBTITLES
    mime_type = "application/x-subrip"
    extension = "srt"
