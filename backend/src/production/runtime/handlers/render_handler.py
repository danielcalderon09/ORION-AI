from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class RenderHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.RENDERING_LONG_FORM})
    artifact_type = ArtifactType.LONG_FORM_RENDER
    mime_type = "video/mp4"
    extension = "mp4"
