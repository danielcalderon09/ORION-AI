"""Durable source reader, handler checkpoints, artifacts, and recovery."""

import asyncio

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.exceptions import (
    ImageAcquisitionManifestChecksumException,
    ImageAcquisitionManifestIncompleteException,
    ImageAcquisitionManifestNotFoundException,
    ImageAcquisitionManifestPathException,
    ImageAcquisitionManifestTypeException,
    SourceImageMissingException,
)
from backend.src.production.video_clip_generation.handler import (
    VideoClipGenerationHandler,
)
from backend.src.production.video_clip_generation.manifest_writer import (
    LocalVideoClipManifestWriter,
)
from backend.src.production.video_clip_generation.media_probe import (
    FFprobeMediaProbe,
    VideoClipIntegrityValidator,
)
from backend.src.production.video_clip_generation.models import (
    VideoClipEntryStatus,
)
from backend.src.production.video_clip_generation.providers.simulated_provider import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.reader import (
    DurableImageAcquisitionManifestReader,
)
from backend.src.production.video_clip_generation.video_store import (
    FilesystemVideoClipBinaryStore,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    MANIFEST_ID,
    NOW,
    VISUAL_ASSET_ID,
    command_context,
    durable_source,
)


class FakeReader:
    def __init__(self, source=None, error=None) -> None:
        self.source = source
        self.error = error
        self.calls = 0

    async def read_for_video_clip_generation(self, *, context):
        self.calls += 1
        if self.error:
            raise self.error
        return self.source


class CountingProvider(SimulatedVideoClipGenerationProvider):
    def __init__(self, error=None) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.error = error

    async def generate_clip(self, request):
        self.calls.append(request.visual_asset_id)
        if self.error:
            raise self.error
        return await super().generate_clip(request)


def video_store(tmp_path):
    integrity = VideoClipIntegrityValidator(
        probe=FFprobeMediaProbe(),
        max_video_bytes=5_000_000,
    )
    return FilesystemVideoClipBinaryStore(
        workspace_root=tmp_path,
        integrity_validator=integrity,
        max_video_bytes=5_000_000,
        clock=lambda: NOW,
    )


def handler(tmp_path, source, provider, *, writer=None):
    return VideoClipGenerationHandler(
        manifest_reader=FakeReader(source),
        provider=provider,
        binary_store=video_store(tmp_path),
        manifest_writer=writer
        or LocalVideoClipManifestWriter(
            tmp_path,
            max_manifest_bytes=200_000,
        ),
        configuration=VideoClipGenerationConfiguration(
            duration_seconds=1,
            frame_rate=24,
        ),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_reader_validates_manifest_source_sidecar_and_allowlist(tmp_path) -> None:
    source, binary_store, repository = await durable_source(tmp_path)
    reader = DurableImageAcquisitionManifestReader(
        workspace_root=tmp_path,
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=200_000,
    )
    _, context = command_context()
    read = await reader.read_for_video_clip_generation(context=context)
    assert read.artifact_id == MANIFEST_ID
    assert read.source_images[0].content == source.source_images[0].content
    assert "unsafe_ignored" not in read.metadata


@pytest.mark.asyncio
async def test_reader_fallback_is_deterministic(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    first = repository.manifests[0]
    older = first.model_copy(
        update={
            "artifact_id": first.artifact_id.__class__(int=99),
            "relative_path": first.relative_path.replace("attempt-1", "attempt-0"),
        }
    )
    repository.manifests = (older, first)
    reader = DurableImageAcquisitionManifestReader(
        workspace_root=tmp_path,
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=200_000,
    )
    _, context = command_context(input_ids=())
    assert (
        await reader.read_for_video_clip_generation(context=context)
    ).artifact_id == first.artifact_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda candidate: candidate.model_copy(
                update={"artifact_type": ArtifactType.PRODUCTION_PLAN}
            ),
            ImageAcquisitionManifestTypeException,
        ),
        (
            lambda candidate: candidate.model_copy(
                update={"relative_path": "../image-acquisition-manifest.json"}
            ),
            ImageAcquisitionManifestPathException,
        ),
        (
            lambda candidate: candidate.model_copy(update={"sha256": "f" * 64}),
            ImageAcquisitionManifestChecksumException,
        ),
    ],
)
async def test_reader_rejects_wrong_type_path_and_checksum(
    tmp_path, mutation, error
) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    repository.manifests = (mutation(repository.manifests[0]),)
    reader = DurableImageAcquisitionManifestReader(
        workspace_root=tmp_path,
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=200_000,
    )
    _, context = command_context(input_ids=())
    with pytest.raises(error):
        await reader.read_for_video_clip_generation(context=context)


@pytest.mark.asyncio
async def test_reader_rejects_missing_manifest_and_source_artifact(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    repository.manifests = ()
    reader = DurableImageAcquisitionManifestReader(
        workspace_root=tmp_path,
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=200_000,
    )
    _, context = command_context(input_ids=())
    with pytest.raises(ImageAcquisitionManifestNotFoundException):
        await reader.read_for_video_clip_generation(context=context)

    _, binary_store, repository = await durable_source(tmp_path / "second")
    repository.image = None
    reader = DurableImageAcquisitionManifestReader(
        workspace_root=tmp_path / "second",
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=200_000,
    )
    with pytest.raises(SourceImageMissingException):
        await reader.read_for_video_clip_generation(context=context)


@pytest.mark.asyncio
async def test_reader_rejects_non_completed_manifest(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    candidate = repository.manifests[0]
    target = tmp_path.joinpath(*candidate.relative_path.split("/"))
    content = target.read_bytes().replace(b'"completed"', b'"in_progress"')
    target.write_bytes(content)
    repository.manifests = (
        candidate.model_copy(
            update={
                "size_bytes": len(content),
                "sha256": __import__("hashlib").sha256(content).hexdigest(),
            }
        ),
    )
    reader = DurableImageAcquisitionManifestReader(
        workspace_root=tmp_path,
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=200_000,
    )
    _, context = command_context(input_ids=())
    with pytest.raises(ImageAcquisitionManifestIncompleteException):
        await reader.read_for_video_clip_generation(context=context)


@pytest.mark.asyncio
async def test_handler_success_artifacts_checkpoints_and_safe_metadata(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = CountingProvider()
    writer = LocalVideoClipManifestWriter(
        tmp_path, max_manifest_bytes=200_000
    )
    command, context = command_context()
    output = await handler(
        tmp_path, source, provider, writer=writer
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [VISUAL_ASSET_ID]
    assert [artifact.artifact_type for artifact in output.artifacts] == [
        ArtifactType.SOURCE_VIDEO_CLIP,
        ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
    ]
    clip = output.artifacts[0]
    assert clip.mime_type == "video/mp4"
    assert clip.metadata["has_audio"] is False
    assert clip.metadata["cost_usd"] is None
    serialized = repr(output)
    assert "ftyp" not in serialized
    assert "base64" not in serialized
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.status.value == "completed"
    assert manifest.summary.stored == 1


@pytest.mark.asyncio
async def test_handler_second_run_and_new_attempt_reuse_without_provider(
    tmp_path,
) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = CountingProvider()
    first_command, first_context = command_context()
    first_handler = handler(tmp_path, source, provider)
    assert (
        await first_handler.execute(first_command, first_context)
    ).result.outcome is StageOutcome.SUCCEEDED
    assert (
        await first_handler.execute(first_command, first_context)
    ).result.outcome is StageOutcome.SUCCEEDED
    second_command, second_context = command_context(attempt=2)
    assert (
        await handler(tmp_path, source, provider).execute(
            second_command, second_context
        )
    ).result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [VISUAL_ASSET_ID]


@pytest.mark.asyncio
async def test_reader_failure_prevents_provider_invocation(tmp_path) -> None:
    provider = CountingProvider()
    command, context = command_context()
    component = VideoClipGenerationHandler(
        manifest_reader=FakeReader(
            error=ImageAcquisitionManifestNotFoundException("missing")
        ),
        provider=provider,
        binary_store=video_store(tmp_path),
        manifest_writer=LocalVideoClipManifestWriter(
            tmp_path, max_manifest_bytes=200_000
        ),
        configuration=VideoClipGenerationConfiguration(duration_seconds=1),
        clock=lambda: NOW,
    )
    output = await component.execute(command, context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert provider.calls == []


@pytest.mark.asyncio
async def test_handler_cancellation_propagates_and_keeps_generating_checkpoint(
    tmp_path,
) -> None:
    source, _, _ = await durable_source(tmp_path)

    class CancelProvider(CountingProvider):
        async def generate_clip(self, request):
            self.calls.append(request.visual_asset_id)
            raise asyncio.CancelledError

    writer = LocalVideoClipManifestWriter(
        tmp_path, max_manifest_bytes=200_000
    )
    command, context = command_context()
    with pytest.raises(asyncio.CancelledError):
        await handler(
            tmp_path, source, CancelProvider(), writer=writer
        ).execute(command, context)
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status.value == "generating"


@pytest.mark.asyncio
async def test_restart_generating_without_clip_becomes_uncertain(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    writer = LocalVideoClipManifestWriter(
        tmp_path, max_manifest_bytes=200_000
    )
    component = handler(tmp_path, source, CountingProvider(), writer=writer)
    command, context = command_context()
    initial = component._initial_manifest(source=source, attempt_number=1)
    generating = initial.model_copy(
        update={
            "entries": (
                initial.entries[0].model_copy(
                    update={"status": VideoClipEntryStatus.GENERATING}
                ),
            ),
        }
    )
    await writer.create(context=context, manifest=generating)
    output = await component.execute(command, context)
    assert output.result.outcome is StageOutcome.NEEDS_USER_ACTION
    current = await writer.read_existing(context=context)
    assert current is not None
    assert current.status.value == "uncertain"
    assert current.entries[0].status.value == "uncertain"
