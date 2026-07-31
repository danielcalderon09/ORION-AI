import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers.base import SimulatedStageHandler
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.scene_planning.exceptions import ProductionScriptReadException
from backend.src.production.scene_planning.ports import ProductionScriptReader
from backend.src.production.scripting.models import ProductionScriptScene


class SubtitleHandler(SimulatedStageHandler):
    supported_stages = frozenset({ProductionStage.GENERATING_SUBTITLES})
    artifact_type = ArtifactType.SUBTITLES
    mime_type = "application/x-subrip"
    extension = "srt"


class DurableSubtitleHandler:
    """Create the minimal deterministic SRT required by the local MVP."""

    supported_stages = frozenset({ProductionStage.GENERATING_SUBTITLES})

    def __init__(
        self,
        *,
        script_reader: ProductionScriptReader,
        workspace_root: Path,
        clock: Callable[[], datetime],
        max_subtitle_bytes: int = 1_000_000,
    ) -> None:
        if max_subtitle_bytes < 1:
            raise ValueError("max_subtitle_bytes must be positive")
        self._script_reader = script_reader
        self._confinement = WorkspaceConfinement(workspace_root)
        self._clock = clock
        self._max_subtitle_bytes = max_subtitle_bytes

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        if command.stage is not ProductionStage.GENERATING_SUBTITLES:
            raise ValueError("handler supports only generating_subtitles")
        if context.command_id != command.command_id or context.job_id != command.job_id:
            raise ValueError("StageContext does not belong to StageCommand")
        started_at = self._aware_now()
        try:
            source = await self._script_reader.read_for_scene_planning(context=context)
            content = _serialize_srt(source.script.scenes)
            if len(content) > self._max_subtitle_bytes:
                raise ValueError("generated subtitles exceed the configured limit")
            relative_path = (
                f"production/{command.job_id}/generating_subtitles/"
                f"attempt-{context.attempt_number}/subtitles.srt"
            )
            size_bytes, sha256 = await asyncio.to_thread(
                self._write_once,
                relative_path,
                content,
            )
        except ProductionScriptReadException as exc:
            return self._failure(command, started_at, "subtitle_source_invalid", str(exc))
        except (OSError, ValueError) as exc:
            return self._failure(command, started_at, "subtitle_write_failed", str(exc))

        artifact = Artifact(
            artifact_id=uuid5(
                NAMESPACE_URL,
                f"orion:subtitles:{command.job_id}:{context.attempt_number}:{sha256}",
            ),
            job_id=command.job_id,
            artifact_type=ArtifactType.SUBTITLES,
            relative_path=relative_path,
            mime_type="application/x-subrip",
            status=ArtifactStatus.READY,
            size_bytes=size_bytes,
            sha256=sha256,
            duration_seconds=source.script.target_duration_seconds,
            provider="orion-simulated-subtitles",
            model_version="deterministic-srt-v1",
            metadata={
                "cue_count": len(source.script.scenes),
                "deterministic": True,
                "simulated": True,
                "source_script_artifact_id": str(source.artifact_id),
                "source_script_sha256": source.sha256,
            },
        )
        finished_at = self._aware_now()
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started_at,
                finished_at=finished_at,
                progress_percent=100,
                output_artifact_ids=(artifact.artifact_id,),
                metadata={
                    "cue_count": len(source.script.scenes),
                    "simulated": True,
                    "subtitle_sha256": sha256,
                },
            ),
            artifacts=(artifact,),
        )

    def _write_once(self, relative_path: str, content: bytes) -> tuple[int, str]:
        target = self._confinement.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(target.parent)
        lock = target.with_suffix(".srt.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise OSError("subtitle output is locked") from exc
        try:
            if target.exists():
                self._confinement.reject_unsafe_file(target)
                existing = target.read_bytes()
                if existing != content:
                    raise OSError("subtitle output already exists with different content")
            else:
                descriptor, name = tempfile.mkstemp(
                    prefix=".subtitles.",
                    suffix=".tmp",
                    dir=target.parent,
                )
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_nlink != 1:
                raise OSError("subtitle output must not be hard-linked")
            return len(content), hashlib.sha256(content).hexdigest()
        finally:
            with suppress(FileNotFoundError):
                lock.unlink()

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        code: str,
        message: str,
    ) -> StageExecutionOutput:
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.FAILED_PERMANENT,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=0,
                error_code=code,
                error_message=message[:500],
                metadata={"simulated": True},
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware timestamps")
        return value


def _serialize_srt(scenes: tuple[ProductionScriptScene, ...]) -> bytes:
    cue_lines: list[str] = []
    start_ms = 0
    for index, scene in enumerate(scenes, start=1):
        duration_seconds = scene.estimated_duration_seconds
        narration = " ".join(scene.narration.split())
        end_ms = start_ms + round(duration_seconds * 1000)
        cue_lines.extend(
            (
                str(index),
                f"{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}",
                narration,
                "",
            )
        )
        start_ms = end_ms
    return ("\n".join(cue_lines).rstrip() + "\n").encode("utf-8")


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
