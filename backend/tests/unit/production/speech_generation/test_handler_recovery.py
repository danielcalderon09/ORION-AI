import asyncio
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.speech_generation.handler import SpeechGenerationHandler
from backend.src.production.speech_generation.manifest_writer import (
    InMemorySpeechManifestWriter,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifestStatus,
    SpeechSegmentStatus,
)
from backend.src.production.speech_generation.providers import (
    SimulatedSpeechGenerationProvider,
)
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


class MutableClock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value


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
