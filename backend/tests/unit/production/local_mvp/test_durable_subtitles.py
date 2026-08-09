import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType, ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers import DurableSubtitleHandler
from backend.src.production.scene_planning.ports import ReadProductionScript
from backend.src.production.scripting.models import ProductionScript, ProductionScriptScene
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifest,
    SpeechGenerationManifestStatus,
    SpeechSegmentManifestEntry,
    SpeechSegmentStatus,
    SpeechTimingProvenance,
    summarize_speech_entries,
)
from backend.src.production.speech_generation.serialization import serialize_speech_manifest

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
JOB_ID = UUID("90000000-0000-4000-8000-000000000011")
COMMAND_ID = UUID("90000000-0000-4000-8000-000000000012")
SCRIPT_ID = UUID("90000000-0000-4000-8000-000000000013")


class ScriptReader:
    async def read_for_scene_planning(self, *, context: StageContext) -> ReadProductionScript:
        assert context.job_id == JOB_ID
        script = ProductionScript(
            source_plan_schema_version="1.0.0",
            title="Marte",
            language="es",
            target_duration_seconds=8,
            tone="informativo",
            opening_hook="Marte sorprende.",
            scenes=(
                ProductionScriptScene(
                    scene_number=1,
                    source_scene_number=1,
                    heading="Uno",
                    narration="Primera curiosidad.",
                    estimated_duration_seconds=4,
                    delivery_style="claro",
                    visual_intent="Marte",
                ),
                ProductionScriptScene(
                    scene_number=2,
                    source_scene_number=2,
                    heading="Dos",
                    narration="Segunda curiosidad.",
                    estimated_duration_seconds=4,
                    delivery_style="claro",
                    visual_intent="Superficie",
                ),
            ),
        )
        return ReadProductionScript(
            script=script,
            artifact_id=SCRIPT_ID,
            relative_path=f"production/{JOB_ID}/scripting/attempt-1/production-script.json",
            sha256="a" * 64,
            size_bytes=100,
            schema_version="1.0.0",
        )


class ArtifactInventory:
    def __init__(self, artifacts: tuple[Artifact, ...]) -> None:
        self.artifacts = artifacts

    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]:
        assert job_id == JOB_ID
        return self.artifacts


def _command_and_context() -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.GENERATING_SUBTITLES,
        attempt_number=1,
        idempotency_key="b" * 64,
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.GENERATING_SUBTITLES,
        attempt_number=1,
        workspace_relative_path=(f"production/{JOB_ID}/generating_subtitles/attempt-1"),
        correlation_id=JOB_ID,
    )
    return command, context


@pytest.mark.asyncio
async def test_durable_subtitle_is_valid_nonempty_and_idempotent(tmp_path) -> None:
    handler = DurableSubtitleHandler(
        script_reader=ScriptReader(),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        clock=lambda: NOW,
    )
    command, context = _command_and_context()
    first = await handler.execute(command, context)
    second = await handler.execute(command, context)
    assert first == second
    assert first.result.outcome is StageOutcome.SUCCEEDED
    assert len(first.artifacts) == 1
    artifact = first.artifacts[0]
    assert artifact.artifact_type is ArtifactType.SUBTITLES
    assert artifact.size_bytes is not None and artifact.size_bytes > 0
    path = tmp_path / artifact.relative_path
    content = path.read_bytes()
    assert content.decode("utf-8") == (
        "1\n00:00:00,000 --> 00:00:04,000\nPrimera curiosidad.\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\nSegunda curiosidad.\n"
    )
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_subtitles_use_actual_per_scene_speech_duration(tmp_path) -> None:
    entries = tuple(
        SpeechSegmentManifestEntry(
            segment_id=f"segment-{index:032x}",
            sequence_index=index - 1,
            source_scene_id=f"scene-{index:03d}",
            narration_text=f"Narración {index}",
            normalized_text_hash=f"{index}" * 64,
            target_duration_ms=4_000,
            timing_provenance=SpeechTimingProvenance.SCRIPT_SCENE_ESTIMATE,
            status=SpeechSegmentStatus.STORED,
            audio_binary_asset_id=f"speech-segment-{index:032x}",
            audio_artifact_id=UUID(f"91000000-0000-4000-8000-{index:012d}"),
            storage_path=f"production/{JOB_ID}/assets/speech/segment-{index}.wav",
            mime_type="audio/wav",
            extension="wav",
            sha256=f"{index}" * 64,
            size_bytes=1_000,
            duration_ms=duration,
            sample_rate_hz=24_000,
            channel_count=1,
            sample_width_bytes=2,
            frame_count=duration * 24,
            provider="simulated",
            generation_attempt_count=1,
            created_at=NOW,
        )
        for index, duration in enumerate((5_000, 3_500), start=1)
    )
    manifest = SpeechGenerationManifest(
        job_id=JOB_ID,
        attempt_number=1,
        source_script_schema_version="1.0.0",
        source_script_artifact_id=SCRIPT_ID,
        source_script_sha256="a" * 64,
        provider="simulated",
        requested_voice="neutral",
        requested_language="es",
        requested_speaking_rate=150,
        configuration_fingerprint="b" * 64,
        entries=entries,
        summary=summarize_speech_entries(entries),
        status=SpeechGenerationManifestStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )
    content = serialize_speech_manifest(manifest)
    relative_path = (
        f"production/{JOB_ID}/generating_narration/attempt-1/"
        "speech-generation-manifest.json"
    )
    target = tmp_path.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    artifact = Artifact(
        job_id=JOB_ID,
        artifact_type=ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
        relative_path=relative_path,
        mime_type="application/json",
        status=ArtifactStatus.READY,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    handler = DurableSubtitleHandler(
        script_reader=ScriptReader(),  # type: ignore[arg-type]
        artifact_inventory=ArtifactInventory((artifact,)),
        workspace_root=tmp_path,
        clock=lambda: NOW,
    )

    output = await handler.execute(*_command_and_context())

    subtitle = tmp_path / output.artifacts[0].relative_path
    assert subtitle.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:05,000\nPrimera curiosidad.\n\n"
        "2\n00:00:05,000 --> 00:00:09,000\nSegunda curiosidad.\n"
    )
    assert output.artifacts[0].duration_seconds == 9
