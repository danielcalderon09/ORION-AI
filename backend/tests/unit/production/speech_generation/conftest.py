from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.scripting.models import (
    ProductionScript,
    ProductionScriptScene,
)
from backend.src.production.speech_generation.audio_store import (
    FilesystemSpeechAudioStore,
)
from backend.src.production.speech_generation.configuration import (
    SpeechGenerationConfiguration,
)
from backend.src.production.speech_generation.duration import simulated_duration_ms
from backend.src.production.speech_generation.models import (
    SpeechAudioWriteRequest,
    SpeechBinaryAssetMetadata,
    SpeechSegmentAudioMetadata,
)
from backend.src.production.speech_generation.ports import (
    ReadSpeechSourceScript,
    SpeechProviderRequest,
)
from backend.src.production.speech_generation.segment_builder import (
    build_speech_segments,
)
from backend.src.production.speech_generation.wav import SpeechWavValidator

NOW = datetime(2026, 7, 26, 15, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000009001")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000009001")
SCRIPT_ID = UUID("30000000-0000-4000-8000-000000009001")
SCRIPT_SHA = "a" * 64


def speech_configuration(**updates: object) -> SpeechGenerationConfiguration:
    values: dict[str, object] = {
        "max_segment_duration_ms": 2_000,
        "max_audio_bytes": 200_000,
        "generating_stale_after_seconds": 1,
    }
    values.update(updates)
    return SpeechGenerationConfiguration(**values)


def source_script(
    *,
    sha256: str = SCRIPT_SHA,
    first_narration: str = "Hola, mundo.",
) -> ReadSpeechSourceScript:
    script = ProductionScript(
        source_plan_schema_version="1.0.0",
        title="Historia",
        language="es-ES",
        target_duration_seconds=1.25,
        tone="sereno",
        opening_hook="Inicio",
        scenes=(
            ProductionScriptScene(
                scene_number=1,
                source_scene_number=1,
                heading="Uno",
                narration=first_narration,
                estimated_duration_seconds=0.5,
                delivery_style="neutral",
                visual_intent="Paisaje",
            ),
            ProductionScriptScene(
                scene_number=2,
                source_scene_number=2,
                heading="Dos",
                narration="Una segunda escena.",
                estimated_duration_seconds=0.75,
                delivery_style="neutral",
                visual_intent="Ciudad",
            ),
        ),
    )
    return ReadSpeechSourceScript(
        script=script,
        artifact_id=SCRIPT_ID,
        relative_path=(f"production/{JOB_ID}/scripting/attempt-1/production-script.json"),
        sha256=sha256,
        size_bytes=1_024,
        schema_version="1.0.0",
        provider="orion-simulated",
        model_version="simulated-script-v1",
        created_at=NOW,
    )


def command_context(*, attempt: int = 1) -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.GENERATING_NARRATION,
        attempt_number=attempt,
        idempotency_key=f"speech:{attempt}",
        input_artifact_ids=(),
        configuration_snapshot={},
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.GENERATING_NARRATION,
        attempt_number=attempt,
        workspace_relative_path=(f"production/{JOB_ID}/generating_narration/attempt-{attempt}"),
        correlation_id=JOB_ID,
    )
    return command, context


def audio_store(
    root: Path,
    configuration: SpeechGenerationConfiguration,
) -> FilesystemSpeechAudioStore:
    return FilesystemSpeechAudioStore(
        workspace_root=root,
        validator=SpeechWavValidator(max_audio_bytes=configuration.max_audio_bytes),
        max_audio_bytes=configuration.max_audio_bytes,
        clock=lambda: NOW,
    )


def speech_requests(
    source: ReadSpeechSourceScript,
    configuration: SpeechGenerationConfiguration,
    *,
    index: int = 0,
) -> tuple[SpeechProviderRequest, SpeechAudioWriteRequest]:
    segment = build_speech_segments(source, configuration)[index]
    provider_request = SpeechProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        segment=segment,
        configuration=configuration,
        fingerprint=configuration.fingerprint(),
    )
    duration_ms = simulated_duration_ms(provider_request)
    frame_count = round(duration_ms * configuration.sample_rate_hz / 1_000)
    write_request = SpeechAudioWriteRequest(
        job_id=JOB_ID,
        segment=segment,
        expected=SpeechSegmentAudioMetadata(
            duration_ms=round(frame_count * 1_000 / configuration.sample_rate_hz),
            sample_rate_hz=configuration.sample_rate_hz,
            frame_count=frame_count,
        ),
        metadata=SpeechBinaryAssetMetadata(
            source_script_artifact_id=source.artifact_id,
            source_script_sha256=source.sha256,
            normalized_text_hash=segment.normalized_text_hash,
            configuration_fingerprint=configuration.fingerprint(),
            provider="orion-simulated-speech",
            requested_voice=segment.requested_voice,
            requested_language=segment.requested_language,
            deterministic=True,
            attributes={"simulated": True},
        ),
    )
    return provider_request, write_request


class FakeSourceReader:
    def __init__(self, source: ReadSpeechSourceScript) -> None:
        self.source = source
        self.calls = 0

    async def read_for_speech_generation(self, *, context):
        self.calls += 1
        return self.source


@pytest.fixture
def source() -> ReadSpeechSourceScript:
    return source_script()
