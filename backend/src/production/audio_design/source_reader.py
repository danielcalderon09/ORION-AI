"""Audio-design adapter for the verified durable production-script reader."""

from typing import cast

from backend.src.production.audio_design.exceptions import (
    AudioDesignSourceIntegrityError,
    AudioDesignSourceNotFoundError,
)
from backend.src.production.audio_design.ports import (
    AudioDesignStageContext,
    ReadAudioDesignSourceScript,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.exceptions import (
    ProductionScriptNotFoundException,
    ProductionScriptReadException,
)
from backend.src.production.scene_planning.ports import ProductionScriptReader


class AudioDesignSourceScriptReaderAdapter:
    def __init__(self, reader: ProductionScriptReader) -> None:
        self._reader = reader

    async def read_for_audio_design(
        self,
        *,
        context: AudioDesignStageContext,
    ) -> ReadAudioDesignSourceScript:
        try:
            source = await self._reader.read_for_scene_planning(context=cast(StageContext, context))
        except ProductionScriptNotFoundException as exc:
            raise AudioDesignSourceNotFoundError(
                "no durable production script is registered"
            ) from exc
        except ProductionScriptReadException as exc:
            raise AudioDesignSourceIntegrityError("durable production script is invalid") from exc
        return ReadAudioDesignSourceScript(
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
