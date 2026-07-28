"""Speech-owned adapter for the verified durable production-script reader."""

from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.exceptions import (
    ProductionScriptNotFoundException,
    ProductionScriptReadException,
)
from backend.src.production.scene_planning.ports import ProductionScriptReader
from backend.src.production.speech_generation.exceptions import (
    SpeechSourceScriptIntegrityError,
    SpeechSourceScriptNotFoundError,
)
from backend.src.production.speech_generation.ports import ReadSpeechSourceScript


class SpeechSourceScriptReaderAdapter:
    """Translate the stable script read model into the speech-owned port."""

    def __init__(self, reader: ProductionScriptReader) -> None:
        self._reader = reader

    async def read_for_speech_generation(
        self,
        *,
        context: StageContext,
    ) -> ReadSpeechSourceScript:
        try:
            source = await self._reader.read_for_scene_planning(context=context)
        except ProductionScriptNotFoundException as exc:
            raise SpeechSourceScriptNotFoundError(
                "no durable production script is registered"
            ) from exc
        except ProductionScriptReadException as exc:
            raise SpeechSourceScriptIntegrityError("durable production script is invalid") from exc
        return ReadSpeechSourceScript(
            script=source.script,
            artifact_id=source.artifact_id,
            relative_path=source.relative_path,
            sha256=source.sha256,
            size_bytes=source.size_bytes,
            schema_version=source.schema_version,
            provider=source.provider,
            model_version=source.model_version,
            created_at=source.created_at,
        )
