import asyncio
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from backend.src.production.application.results import StageOutcome
from backend.src.production.composition.audio_first_duration_reader import (
    DurableSpeechDurationResolutionReader,
)
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.speech_generation.exceptions import (
    SpeechReplacementLineageError,
)
from backend.src.production.speech_generation.handler import SpeechGenerationHandler
from backend.src.production.speech_generation.manifest_writer import (
    InMemorySpeechManifestWriter,
    LocalSpeechManifestWriter,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifestStatus,
    SpeechSegmentAudioMetadata,
    SpeechSegmentStatus,
)
from backend.src.production.speech_generation.ports import SpeechProviderResult
from backend.src.production.speech_generation.providers import (
    SimulatedSpeechGenerationProvider,
)
from backend.src.production.speech_generation.providers.simulated_provider import _render_wav
from backend.tests.unit.production.speech_generation.conftest import (
    NOW,
    FakeSourceReader,
    audio_store,
    command_context,
    source_script,
    speech_configuration,
)


class CountingProvider(SimulatedSpeechGenerationProvider):
    def __init__(self, *, fail_on: int | None = None) -> None:
        super().__init__()
        self.calls = 0
        self.fail_on = fail_on

    async def generate(self, request):
        self.calls += 1
        if self.calls == self.fail_on:
            from backend.src.production.speech_generation.exceptions import (
                SpeechProviderResponseError,
            )

            raise SpeechProviderResponseError("safe failure")
        return await super().generate(request)


class BlockingProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await SimulatedSpeechGenerationProvider.generate(self, request)


class LineageBlockedProvider(SimulatedSpeechGenerationProvider):
    async def generate(self, request):
        raise SpeechReplacementLineageError("offline lineage rejection")


class MutableClock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value


class ArtifactInventory:
    def __init__(self, artifacts) -> None:
        self.artifacts = artifacts

    async def list_for_job(self, job_id):
        return tuple(item for item in self.artifacts if item.job_id == job_id)


class NaturalDurationProvider:
    name = "openrouter"

    def __init__(self, durations_ms: tuple[int, ...]) -> None:
        self.durations = iter(durations_ms)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        duration_ms = next(self.durations)
        frame_count = round(duration_ms * request.configuration.sample_rate_hz / 1_000)
        return SpeechProviderResult(
            content=_render_wav(
                request.segment.normalized_text_hash,
                sample_rate_hz=request.configuration.sample_rate_hz,
                frame_count=frame_count,
            ),
            provider="openrouter",
            audio=SpeechSegmentAudioMetadata(
                duration_ms=duration_ms,
                sample_rate_hz=request.configuration.sample_rate_hz,
                frame_count=frame_count,
            ),
            deterministic=False,
            metadata={"network": False},
        )

    async def close(self) -> None:
        return None


def _handler(
    tmp_path: Path,
    *,
    reader,
    provider,
    writer,
    clock=lambda: NOW,
):
    configuration = speech_configuration()
    return SpeechGenerationHandler(
        script_reader=reader,
        provider=provider,
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=writer,
        configuration=configuration,
        clock=clock,
    )


def _eight_second_source():
    source = source_script()
    scenes = tuple(
        scene.model_copy(update={"estimated_duration_seconds": 4.0})
        for scene in source.script.scenes
    )
    return source.model_copy(
        update={"script": source.script.model_copy(update={"target_duration_seconds": 8.0, "scenes": scenes})}
        )


async def test_local_lineage_rejection_is_not_provider_uncertainty(tmp_path: Path) -> None:
    configuration = speech_configuration()
    writer = InMemorySpeechManifestWriter()
    handler = SpeechGenerationHandler(
        script_reader=FakeSourceReader(source_script()),
        provider=LineageBlockedProvider(),
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=writer,
        configuration=configuration,
        clock=lambda: NOW,
    )
    command, context = command_context()
    result = await handler.execute(command, context)
    manifest = await writer.read_existing(context=context)
    assert result.result.error_code == "speech_replacement_lineage_blocked"
    assert manifest is not None
    assert manifest.entries[0].status is SpeechSegmentStatus.FAILED
    assert manifest.entries[0].error_code == "speech_replacement_lineage_blocked"


async def test_natural_durations_are_checkpointed_before_video(tmp_path: Path) -> None:
    configuration = speech_configuration(
        provider="openrouter",
        max_segment_duration_ms=6_000,
        max_audio_bytes=300_000,
    )
    provider = NaturalDurationProvider((4_250, 5_000))
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()
    result = await SpeechGenerationHandler(
        script_reader=FakeSourceReader(_eight_second_source()),
        provider=provider,
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=writer,
        configuration=configuration,
        clock=lambda: NOW,
    ).execute(command, context)

    manifest = await writer.read_existing(context=context)
    assert result.result.outcome is StageOutcome.SUCCEEDED
    assert manifest is not None and manifest.duration_resolution is not None
    assert manifest.duration_resolution.resolved_duration_ms == 9_250
    assert tuple(
        scene.resolved_duration_ms for scene in manifest.duration_resolution.scenes
    ) == (4_250, 5_000)
    assert provider.calls == 2


async def test_completed_speech_resolution_is_readable_by_video_integration(
    tmp_path: Path,
) -> None:
    configuration = speech_configuration()
    writer = LocalSpeechManifestWriter(tmp_path, max_manifest_bytes=200_000)
    command, context = command_context()
    output = await SpeechGenerationHandler(
        script_reader=FakeSourceReader(source_script()),
        provider=SimulatedSpeechGenerationProvider(),
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=writer,
        configuration=configuration,
        clock=lambda: NOW,
    ).execute(command, context)
    manifest_artifact = next(
        artifact
        for artifact in output.artifacts
        if artifact.artifact_type is ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST
    )
    reader = DurableSpeechDurationResolutionReader(
        workspace_root=tmp_path,
        inventory=ArtifactInventory((manifest_artifact,)),
        max_manifest_bytes=200_000,
    )

    resolution = await reader.read_for_job(command.job_id)

    assert resolution is not None
    assert resolution.requested_target_duration_ms == 1_250
    assert resolution.resolved_duration_ms == 1_250


async def test_excessive_narration_fails_durably_and_retry_reuses_audio(
    tmp_path: Path,
) -> None:
    configuration = speech_configuration(
        provider="openrouter",
        max_segment_duration_ms=6_000,
        max_audio_bytes=300_000,
    )
    provider = NaturalDurationProvider((6_000, 6_000))
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()
    handler = SpeechGenerationHandler(
        script_reader=FakeSourceReader(_eight_second_source()),
        provider=provider,
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=writer,
        configuration=configuration,
        clock=lambda: NOW,
    )

    first = await handler.execute(command, context)
    second = await handler.execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert first.result.error_code == "duration_resolution_invalid"
    assert second.result.error_code == "duration_resolution_invalid"
    assert provider.calls == 2
    assert manifest is not None and manifest.duration_resolution is not None
    assert manifest.duration_resolution.resolved_duration_ms == 12_000
    assert not manifest.duration_resolution.accepted


async def test_successful_multi_segment_generation_and_duplicate_delivery(
    tmp_path: Path,
) -> None:
    reader = FakeSourceReader(source_script())
    provider = CountingProvider()
    writer = InMemorySpeechManifestWriter()
    handler = _handler(tmp_path, reader=reader, provider=provider, writer=writer)
    command, context = command_context()

    first = await handler.execute(command, context)
    second = await handler.execute(command, context)

    assert first.result.outcome is StageOutcome.SUCCEEDED
    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == 2
    assert len(first.artifacts) == 3
    assert tuple(item.artifact_type for item in first.artifacts) == (
        ArtifactType.NARRATION,
        ArtifactType.NARRATION,
        ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
    )
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.status is SpeechGenerationManifestStatus.COMPLETED
    assert manifest.summary.stored == 2
    assert manifest.summary.total_duration_ms == 1_250


async def test_partial_failure_is_checkpointed_and_retry_resumes(
    tmp_path: Path,
) -> None:
    reader = FakeSourceReader(source_script())
    writer = InMemorySpeechManifestWriter()
    failing = CountingProvider(fail_on=2)
    command, context = command_context()
    first = await _handler(
        tmp_path,
        reader=reader,
        provider=failing,
        writer=writer,
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert first.result.outcome is StageOutcome.FAILED_PERMANENT
    assert manifest is not None
    assert manifest.status is SpeechGenerationManifestStatus.PARTIAL
    assert tuple(entry.status for entry in manifest.entries) == (
        SpeechSegmentStatus.STORED,
        SpeechSegmentStatus.FAILED,
    )

    retry_provider = CountingProvider()
    result = await _handler(
        tmp_path,
        reader=reader,
        provider=retry_provider,
        writer=writer,
    ).execute(command, context)
    assert result.result.outcome is StageOutcome.SUCCEEDED
    assert retry_provider.calls == 1


async def test_cancellation_leaves_generating_checkpoint_and_restart_recovers(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    reader = FakeSourceReader(source_script())
    writer = InMemorySpeechManifestWriter()
    blocking = BlockingProvider()
    command, context = command_context()
    task = asyncio.create_task(
        _handler(
            tmp_path,
            reader=reader,
            provider=blocking,
            writer=writer,
            clock=clock,
        ).execute(command, context)
    )
    await blocking.started.wait()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status is SpeechSegmentStatus.GENERATING

    immediate_provider = CountingProvider()
    immediate = await _handler(
        tmp_path,
        reader=reader,
        provider=immediate_provider,
        writer=writer,
        clock=clock,
    ).execute(command, context)
    assert immediate.result.outcome is StageOutcome.FAILED_TRANSIENT
    assert immediate_provider.calls == 0

    clock.value += timedelta(seconds=2)
    recovered_provider = CountingProvider()
    recovered = await _handler(
        tmp_path,
        reader=reader,
        provider=recovered_provider,
        writer=writer,
        clock=clock,
    ).execute(command, context)
    assert recovered.result.outcome is StageOutcome.SUCCEEDED
    assert recovered_provider.calls == 2


async def test_concurrent_duplicate_does_not_submit_same_segment_twice(
    tmp_path: Path,
) -> None:
    reader = FakeSourceReader(source_script())
    writer = InMemorySpeechManifestWriter()
    blocking = BlockingProvider()
    handler = _handler(tmp_path, reader=reader, provider=blocking, writer=writer)
    command, context = command_context()

    first = asyncio.create_task(handler.execute(command, context))
    await blocking.started.wait()
    duplicate = await handler.execute(command, context)
    assert duplicate.result.outcome is StageOutcome.FAILED_TRANSIENT
    assert blocking.calls == 1
    blocking.release.set()
    completed = await first
    assert completed.result.outcome is StageOutcome.SUCCEEDED
    assert blocking.calls == 2


async def test_source_script_change_fails_closed_without_regeneration(
    tmp_path: Path,
) -> None:
    reader = FakeSourceReader(source_script())
    provider = CountingProvider()
    writer = InMemorySpeechManifestWriter()
    handler = _handler(tmp_path, reader=reader, provider=provider, writer=writer)
    command, context = command_context()
    assert (await handler.execute(command, context)).result.outcome is StageOutcome.SUCCEEDED

    reader.source = source_script(sha256="b" * 64)
    result = await handler.execute(command, context)
    assert result.result.outcome is StageOutcome.FAILED_PERMANENT
    assert provider.calls == 2
