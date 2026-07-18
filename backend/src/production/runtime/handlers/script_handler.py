from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.handlers.base import SimulatedStageHandler


class ScriptHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.SCRIPTING})
    artifact_type = ArtifactType.MANIFEST
    mime_type = "text/plain"
    extension = "txt"
