"""Handler error mapping, sequential execution, checkpoints, and recovery."""

from uuid import UUID

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.image_acquisition.models import (
    ProductionImageAcquisitionManifest,
)
from backend.src.production.image_acquisition.models import (
    summarize_entries as summarize_image_entries,
)
from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoInvalidRequestError,
    VideoClipNotFoundError,
    VideoClipProviderDependencyException,
    VideoClipProviderResponseException,
    VideoClipProviderTimeoutException,
)
from backend.src.production.video_clip_generation.manifest_writer import (
    InMemoryVideoClipManifestWriter,
    LocalVideoClipManifestWriter,
)
from backend.src.production.video_clip_generation.models import (
    VideoClipEntryStatus,
)
from backend.src.production.video_clip_generation.ports import (
    ReadImageAcquisitionManifest,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    VISUAL_ASSET_ID,
    command_context,
    durable_source,
)
from backend.tests.unit.production.video_clip_generation.test_reader_and_handler import (
    CountingProvider,
    handler,
)


class MissingCountingStore:
    def __init__(self) -> None:
        self.write_calls = 0

    async def resolve(self, *, job_id, visual_asset_id):
        raise VideoClipNotFoundError("missing")

    async def write(self, *, request, content):
        self.write_calls += 1
        raise AssertionError("store must not be called after provider failure")

    async def read(self, *, asset):
        raise AssertionError("store must not be called after provider failure")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome", "entry_status", "error_code"),
    (
        (
            VideoClipProviderTimeoutException("timeout"),
            StageOutcome.FAILED_TRANSIENT,
            VideoClipEntryStatus.FAILED_TRANSIENT,
            "video_provider_timeout",
        ),
        (
            VideoClipProviderDependencyException("missing"),
            StageOutcome.FAILED_PERMANENT,
            VideoClipEntryStatus.FAILED_PERMANENT,
            "video_dependency_unavailable",
        ),
        (
            VideoClipProviderResponseException("invalid"),
            StageOutcome.FAILED_PERMANENT,
            VideoClipEntryStatus.FAILED_PERMANENT,
            "video_clip_invalid",
        ),
    ),
)
async def test_provider_failure_checkpoints_without_binary_write(
    tmp_path,
    error,
    outcome,
    entry_status,
    error_code,
) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = CountingProvider(error=error)
    writer = InMemoryVideoClipManifestWriter()
    component = handler(tmp_path, source, provider, writer=writer)
    store = MissingCountingStore()
    component._store = store
    command, context = command_context()
    output = await component.execute(command, context)
    assert output.result.outcome is outcome
    assert output.result.error_code == error_code
    assert store.write_calls == 0
    assert writer.checkpoint_count == 3
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status is entry_status


@pytest.mark.asyncio
async def test_provider_http_failure_preserves_safe_leaf_diagnostic(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    error = OpenRouterVideoInvalidRequestError(
        "OpenRouter video submit failed with HTTP 400",
        diagnostic_phase="provider_submit",
        diagnostic_code="video_reference_asset_invalid",
        diagnostic_metadata={
            "provider_http_status": 400,
            "provider_operation": "submit",
            "provider_error_code": "reference_image_unreachable",
            "provider_error_body_bytes": 123,
            "provider_error_body_sha256": "a" * 64,
        },
    )
    error.http_status = 400
    writer = InMemoryVideoClipManifestWriter()
    component = handler(
        tmp_path,
        source,
        CountingProvider(error=error),
        writer=writer,
    )
    command, context = command_context()

    output = await component.execute(command, context)

    assert output.result.error_code == "video_reference_asset_invalid"
    assert output.result.metadata["diagnostic_code"] == (
        "video_reference_asset_invalid"
    )
    assert output.result.metadata["provider_http_status"] == 400
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.error_code == "video_reference_asset_invalid"
    assert entry.metadata["provider_error_code"] == "reference_image_unreachable"
    assert "OpenRouter video submit failed" not in repr(entry.metadata)


@pytest.mark.asyncio
async def test_handler_processes_multiple_images_sequentially(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    first_entry = source.manifest.entries[0]
    second_visual_id = "asset-s001-q002-v001"
    second_artifact_id = UUID("40000000-0000-4000-8000-000000001002")
    second_entry = first_entry.model_copy(
        update={
            "visual_asset_id": second_visual_id,
            "binary_asset_id": f"image-{second_visual_id}",
            "binary_artifact_id": second_artifact_id,
            "source_shot_id": "scene-001-shot-002",
            "shot_number": 2,
            "storage_path": first_entry.storage_path.replace(
                VISUAL_ASSET_ID,
                second_visual_id,
            ),
        }
    )
    image_entries = (first_entry, second_entry)
    manifest_payload = source.manifest.model_dump(mode="python")
    manifest_payload.update(
        entries=image_entries,
        summary=summarize_image_entries(image_entries),
    )
    image_manifest = ProductionImageAcquisitionManifest.model_validate(manifest_payload)
    first_image = source.source_images[0]
    second_image = first_image.model_copy(
        update={
            "visual_asset_id": second_visual_id,
            "artifact_id": second_artifact_id,
            "binary_asset_id": f"image-{second_visual_id}",
            "shot_id": "scene-001-shot-002",
            "shot_number": 2,
        }
    )
    multi_source = ReadImageAcquisitionManifest(
        manifest=image_manifest,
        job_id=source.job_id,
        artifact_id=source.artifact_id,
        sha256=source.sha256,
        size_bytes=source.size_bytes,
        schema_version=source.schema_version,
        source_images=(first_image, second_image),
    )
    provider = CountingProvider()
    command, context = command_context()
    output = await handler(tmp_path, multi_source, provider).execute(
        command,
        context,
    )
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [VISUAL_ASSET_ID, second_visual_id]
    assert len(output.artifacts) == 3


@pytest.mark.asyncio
async def test_generating_entry_with_valid_clip_is_recovered(tmp_path) -> None:
    source, _, _ = await durable_source(tmp_path)
    first_provider = CountingProvider()
    first_command, first_context = command_context()
    assert (
        await handler(tmp_path, source, first_provider).execute(
            first_command,
            first_context,
        )
    ).result.outcome is StageOutcome.SUCCEEDED

    second_command, second_context = command_context(attempt=2)
    second_writer = LocalVideoClipManifestWriter(
        tmp_path,
        max_manifest_bytes=200_000,
    )
    second_provider = CountingProvider()
    component = handler(
        tmp_path,
        source,
        second_provider,
        writer=second_writer,
    )
    initial = component._initial_manifest(source=source, attempt_number=2)
    generating = initial.model_copy(
        update={
            "entries": (
                initial.entries[0].model_copy(update={"status": VideoClipEntryStatus.GENERATING}),
            )
        }
    )
    await second_writer.create(context=second_context, manifest=generating)
    output = await component.execute(second_command, second_context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert second_provider.calls == []
    recovered = await second_writer.read_existing(context=second_context)
    assert recovered is not None
    assert recovered.entries[0].status is VideoClipEntryStatus.STORED
    assert recovered.entries[0].metadata["recovered"] is True


@pytest.mark.asyncio
async def test_recovery_rejects_changed_source_manifest_without_provider(
    tmp_path,
) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = CountingProvider()
    first_command, first_context = command_context()
    assert (
        await handler(tmp_path, source, provider).execute(
            first_command,
            first_context,
        )
    ).result.outcome is StageOutcome.SUCCEEDED
    changed = source.model_copy(
        update={
            "artifact_id": UUID("30000000-0000-4000-8000-000000001099"),
            "sha256": "f" * 64,
        }
    )
    second_command, second_context = command_context(attempt=2)
    output = await handler(tmp_path, changed, provider).execute(
        second_command,
        second_context,
    )
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert provider.calls == [VISUAL_ASSET_ID]


@pytest.mark.asyncio
async def test_handler_rejects_unsupported_stage(tmp_path) -> None:
    source = None
    provider = CountingProvider()
    component = handler(tmp_path, source, provider)
    command, context = command_context()
    with pytest.raises(ValueError):
        await component.execute(
            command.model_copy(update={"stage": ProductionStage.SCRIPTING}),
            context,
        )
