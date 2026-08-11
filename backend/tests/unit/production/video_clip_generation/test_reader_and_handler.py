"""Durable source reader, handler checkpoints, artifacts, and recovery."""

import asyncio
from decimal import Decimal

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.duration_resolution import ResolvedSceneDuration
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.planning.provider_budget_planner import (
    BoundVisualShot,
    build_bound_video_purchase_plan,
)
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
from backend.src.production.video_clip_generation.ports import VideoClipJobPreflight
from backend.src.production.video_clip_generation.providers.simulated_provider import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.reader import (
    DurableImageAcquisitionManifestReader,
    _resolved_duration_by_shot,
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
    nine_second_two_shot_source,
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


class PreflightCountingProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.preflight_calls = 0

    async def preflight_job(self, requests):
        self.preflight_calls += 1
        return requests


class DurablePurchasePlanProvider(SimulatedVideoClipGenerationProvider):
    def __init__(self, *, cancel_visual_asset_id: str | None = None) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.preflight_existing_fingerprints: list[str | None] = []
        self._cancel_visual_asset_id = cancel_visual_asset_id

    async def preflight_job(self, requests, existing_plan=None):
        plan = build_bound_video_purchase_plan(
            shots=tuple(
                BoundVisualShot(
                    scene_id=request.scene_id,
                    scene_sequence_index=int(request.scene_id[-3:]) - 1,
                    shot_id=request.shot_id,
                    shot_sequence_index=int(request.shot_id[-3:]) - 1,
                    visual_asset_id=request.visual_asset_id,
                    visual_intent_sha256=request.visual_intent_sha256,
                    source_image_sha256=request.source_image_sha256,
                    usable_duration_ms=round(request.duration_seconds * 1_000),
                )
                for request in requests
            ),
            provider="openrouter",
            model="test/video-model",
            supported_durations_seconds=(4, 6, 8),
            price_per_second_usd=Decimal("0.03"),
            max_requests_per_job=2,
            maximum_authorized_cost_per_request_usd=Decimal("0.20"),
            maximum_authorized_cost_usd=Decimal("0.30"),
        )
        self.preflight_existing_fingerprints.append(
            existing_plan.fingerprint() if existing_plan is not None else None
        )
        if existing_plan is not None and existing_plan.fingerprint() != plan.fingerprint():
            raise AssertionError("purchase plan drifted during recovery")
        clips = tuple(clip for scene in plan.scenes for clip in scene.clips)
        adjusted = tuple(
            request.model_copy(update={"duration_seconds": clip.provider_duration_seconds})
            for request, clip in zip(requests, clips, strict=True)
        )
        return VideoClipJobPreflight(
            requests=adjusted,
            purchase_plan=plan,
            purchase_plan_fingerprint=plan.fingerprint(),
        )

    async def generate_clip(self, request):
        self.calls.append(request.visual_asset_id)
        if request.visual_asset_id == self._cancel_visual_asset_id:
            self._cancel_visual_asset_id = None
            raise asyncio.CancelledError
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
    assert read.source_images[0].metadata["simulated"] is True
    assert read.source_images[0].metadata["provider"] == "orion-simulated"
    assert read.source_images[0].metadata["model"] == "simulated-image-v1"
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
async def test_resolved_scene_duration_is_allocated_once_across_distinct_shots(
    tmp_path,
) -> None:
    source = await nine_second_two_shot_source(tmp_path)
    resolution = ResolvedSceneDuration(
        scene_id="scene-001",
        sequence_index=0,
        planned_duration_ms=9_000,
        actual_narration_duration_ms=9_000,
        resolved_duration_ms=9_000,
    )

    allocated = _resolved_duration_by_shot(
        list(source.source_images),
        {"scene-001": resolution},
    )

    assert allocated == {
        "scene-001-shot-001": 6_000,
        "scene-001-shot-002": 3_000,
    }
    assert sum(allocated.values()) == resolution.resolved_duration_ms


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
async def test_reader_rejects_wrong_type_path_and_checksum(tmp_path, mutation, error) -> None:
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
    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=200_000)
    command, context = command_context()
    output = await handler(tmp_path, source, provider, writer=writer).execute(command, context)
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
async def test_handler_uses_resolved_narration_duration_for_provider_request(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    image = source.source_images[0].model_copy(
        update={
            "resolved_duration_seconds": 1.5,
            "actual_narration_duration_ms": 1_500,
        }
    )
    source = source.model_copy(update={"source_images": (image,)})
    provider = CountingProvider()
    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=200_000)
    command, context = command_context()

    output = await handler(tmp_path, source, provider, writer=writer).execute(command, context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].resolved_scene_duration_ms == 1_500
    assert manifest.entries[0].requested_duration_seconds == 1.5


@pytest.mark.asyncio
async def test_completed_legacy_clip_is_reused_when_audio_resolution_appears(
    tmp_path,
) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = CountingProvider()
    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=200_000)
    command, context = command_context()
    first_handler = handler(tmp_path, source, provider, writer=writer)
    assert (await first_handler.execute(command, context)).result.outcome is (
        StageOutcome.SUCCEEDED
    )

    resolved_image = source.source_images[0].model_copy(
        update={"resolved_duration_seconds": 1.5, "actual_narration_duration_ms": 1_500}
    )
    resolved_source = source.model_copy(update={"source_images": (resolved_image,)})
    second = await handler(
        tmp_path,
        resolved_source,
        provider,
        writer=writer,
    ).execute(command, context)

    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [VISUAL_ASSET_ID]


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
        await handler(tmp_path, source, provider).execute(second_command, second_context)
    ).result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [VISUAL_ASSET_ID]


@pytest.mark.asyncio
async def test_new_attempt_recovers_clip_before_paid_batch_preflight(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = PreflightCountingProvider()
    first_command, first_context = command_context()
    assert (
        await handler(tmp_path, source, provider).execute(first_command, first_context)
    ).result.outcome is StageOutcome.SUCCEEDED

    second_command, second_context = command_context(attempt=2)
    assert (
        await handler(tmp_path, source, provider).execute(second_command, second_context)
    ).result.outcome is StageOutcome.SUCCEEDED

    assert provider.preflight_calls == 1
    assert provider.calls == [VISUAL_ASSET_ID]


@pytest.mark.asyncio
async def test_partial_multi_shot_retry_reuses_clip_and_immutable_purchase_plan(
    tmp_path,
) -> None:
    source = await nine_second_two_shot_source(tmp_path)
    second_visual_id = source.source_images[1].visual_asset_id
    provider = DurablePurchasePlanProvider(cancel_visual_asset_id=second_visual_id)
    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=500_000)
    first_command, first_context = command_context()

    with pytest.raises(asyncio.CancelledError):
        await handler(
            tmp_path,
            source,
            provider,
            writer=writer,
        ).execute(first_command, first_context)

    first_manifest = await writer.read_existing(context=first_context)
    assert first_manifest is not None
    assert first_manifest.purchase_plan is not None
    assert first_manifest.entries[0].status is VideoClipEntryStatus.STORED
    assert first_manifest.entries[1].status is VideoClipEntryStatus.GENERATING

    second_command, second_context = command_context(attempt=2)
    output = await handler(
        tmp_path,
        source,
        provider,
        writer=writer,
    ).execute(second_command, second_context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [VISUAL_ASSET_ID, second_visual_id, second_visual_id]
    second_manifest = await writer.read_existing(context=second_context)
    assert second_manifest is not None
    assert second_manifest.purchase_plan_fingerprint == (
        first_manifest.purchase_plan_fingerprint
    )
    assert provider.preflight_existing_fingerprints == [
        None,
        first_manifest.purchase_plan_fingerprint,
    ]


@pytest.mark.asyncio
async def test_reader_failure_prevents_provider_invocation(tmp_path) -> None:
    provider = CountingProvider()
    command, context = command_context()
    component = VideoClipGenerationHandler(
        manifest_reader=FakeReader(error=ImageAcquisitionManifestNotFoundException("missing")),
        provider=provider,
        binary_store=video_store(tmp_path),
        manifest_writer=LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=200_000),
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

    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=200_000)
    command, context = command_context()
    with pytest.raises(asyncio.CancelledError):
        await handler(tmp_path, source, CancelProvider(), writer=writer).execute(command, context)
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status.value == "generating"


@pytest.mark.asyncio
async def test_restart_generating_without_clip_becomes_uncertain(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=200_000)
    component = handler(tmp_path, source, CountingProvider(), writer=writer)
    command, context = command_context()
    initial = component._initial_manifest(source=source, attempt_number=1)
    generating = initial.model_copy(
        update={
            "entries": (
                initial.entries[0].model_copy(update={"status": VideoClipEntryStatus.GENERATING}),
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
