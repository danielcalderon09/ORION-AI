from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class AssetHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.ACQUIRING_ASSETS})
    artifact_type = ArtifactType.SOURCE_IMAGE
    mime_type = "image/png"
    extension = "png"
