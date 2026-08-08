"""Handler, binary integration, checkpoints, and recovery tests."""

import asyncio
from datetime import UTC, datetime

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.filesystem_store import (
    FilesystemBinaryAssetStore,
)
from backend.src.production.binary_assets.validators import (
    AssetHashValidator,
    AssetMimeValidator,
    AssetSizeValidator,
    BinaryAssetIntegrityValidator,
)
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderTimeoutException,
    ProductionVisualAssetPlanChecksumException,
)
from backend.src.production.image_acquisition.handler import ImageAcquisitionHandler
from backend.src.production.image_acquisition.manifest_writer import (
    InMemoryImageAcquisitionManifestWriter,
    LocalImageAcquisitionManifestWriter,
)
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
)
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.providers import (
    SimulatedImageAcquisitionProvider,
)
from backend.src.production.visual_asset_planning.models import AssetKind
from backend.tests.unit.production.image_acquisition.conftest import NOW


class FakeReader:
    def __init__(self, source=None, error=None) -> None:
        self.source = source
        self.error = error
        self.calls = 0

    async def read_for_image_acquisition(self, *, context):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.source


class CountingProvider(SimulatedImageAcquisitionProvider):
    def __init__(self, error=None) -> None:
        self.calls = []
        self.error = error

    async def generate_image(self, request):
        self.calls.append(request.visual_asset.asset_id)
        if self.error is not None:
            raise self.error
        return await super().generate_image(request)


class ForbiddenBinaryWriter:
    async def write(self, *, request, content):
        raise AssertionError("binary writer must not be invoked")


def store(tmp_path) -> FilesystemBinaryAssetStore:
    configuration = AssetStorageConfiguration(
        workspace=tmp_path,
        max_asset_size=1_000_000,
        allowed_mime_types=frozenset(
            {"image/png", "image/jpeg", "image/webp"}
        ),
        allowed_extensions=frozenset({"png", "jpg", "jpeg", "webp"}),
    )
    integrity = BinaryAssetIntegrityValidator(
        mime_validator=AssetMimeValidator(configuration),
        hash_validator=AssetHashValidator(),
        size_validator=AssetSizeValidator(configuration),
    )
    return FilesystemBinaryAssetStore(
        configuration=configuration,
        integrity_validator=integrity,
        clock=lambda: NOW,
    )


def handler(
    *,
    source,
    provider,
    manifest_writer,
    binary_store,
) -> ImageAcquisitionHandler:
    return ImageAcquisitionHandler(
        plan_reader=FakeReader(source),
        provider=provider,
        manifest_writer=manifest_writer,
        binary_reader=binary_store,
        binary_writer=binary_store,
        configuration=ImageAcquisitionConfiguration(),
        provider_name="simulated",
        requested_model=None,
        prompt_builder=ImageGenerationPromptBuilder(),
        clock=lambda: datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_success_writes_images_manifest_and_safe_artifacts(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    provider = CountingProvider()
    manifest_writer = LocalImageAcquisitionManifestWriter(
        tmp_path,
        max_manifest_bytes=200_000,
    )
    binary_store = store(tmp_path)
    command, context = image_command_context
    output = await handler(
        source=source_visual_plan,
        provider=provider,
        manifest_writer=manifest_writer,
        binary_store=binary_store,
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == [
        "asset-s001-q001-v001",
        "asset-s001-q002-v001",
    ]
    assert [item.artifact_type for item in output.artifacts] == [
        ArtifactType.SOURCE_IMAGE,
        ArtifactType.SOURCE_IMAGE,
        ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST,
    ]
    for artifact in output.artifacts:
        assert "content" not in artifact.metadata
        assert "base64" not in artifact.metadata
        assert "prompt" not in artifact.metadata
        assert artifact.relative_path.startswith(f"production/{command.job_id}/")
    manifest = await manifest_writer.read_existing(context=context)
    assert manifest is not None
    assert all(
        entry.status is ImageAcquisitionEntryStatus.STORED
        for entry in manifest.entries
    )
    assert (
        tmp_path
        / "production"
        / str(command.job_id)
        / "acquiring_assets"
        / "attempt-1"
        / "image-acquisition-manifest.json"
    ).is_file()


@pytest.mark.asyncio
async def test_second_execution_recovers_without_provider(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    provider = CountingProvider()
    manifest_writer = InMemoryImageAcquisitionManifestWriter()
    binary_store = store(tmp_path)
    command, context = image_command_context
    acquired = handler(
        source=source_visual_plan,
        provider=provider,
        manifest_writer=manifest_writer,
        binary_store=binary_store,
    )
    first = await acquired.execute(command, context)
    assert first.result.outcome is StageOutcome.SUCCEEDED
    provider.calls.clear()
    second = await acquired.execute(command, context)
    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == []
    assert all(
        artifact.metadata.get("recovered") is True
        for artifact in second.artifacts
        if artifact.artifact_type is ArtifactType.SOURCE_IMAGE
    )


@pytest.mark.asyncio
async def test_retry_attempt_reuses_verified_binary(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    provider = CountingProvider()
    binary_store = store(tmp_path)
    command, context = image_command_context
    first = handler(
        source=source_visual_plan,
        provider=provider,
        manifest_writer=InMemoryImageAcquisitionManifestWriter(),
        binary_store=binary_store,
    )
    assert (await first.execute(command, context)).result.outcome is StageOutcome.SUCCEEDED
    provider.calls.clear()
    retry_command = command.model_copy(update={"attempt_number": 2})
    retry_context = context.model_copy(
        update={
            "attempt_number": 2,
            "workspace_relative_path": (
                f"production/{command.job_id}/acquiring_assets/attempt-2"
            ),
        }
    )
    second = handler(
        source=source_visual_plan,
        provider=provider,
        manifest_writer=InMemoryImageAcquisitionManifestWriter(),
        binary_store=binary_store,
    )
    output = await second.execute(retry_command, retry_context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == []
    assert all(
        artifact.metadata.get("recovered") is True
        for artifact in output.artifacts
        if artifact.artifact_type is ArtifactType.SOURCE_IMAGE
    )


@pytest.mark.asyncio
async def test_reader_failure_prevents_provider_and_writer(
    source_visual_plan,
    image_command_context,
) -> None:
    provider = CountingProvider()
    reader = FakeReader(
        error=ProductionVisualAssetPlanChecksumException("checksum")
    )
    command, context = image_command_context
    acquired = ImageAcquisitionHandler(
        plan_reader=reader,
        provider=provider,
        manifest_writer=InMemoryImageAcquisitionManifestWriter(),
        binary_reader=ForbiddenBinaryWriter(),
        binary_writer=ForbiddenBinaryWriter(),
        configuration=ImageAcquisitionConfiguration(),
        provider_name="simulated",
        requested_model=None,
        prompt_builder=ImageGenerationPromptBuilder(),
        clock=lambda: datetime.now(UTC),
    )
    output = await acquired.execute(command, context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert provider.calls == []


@pytest.mark.asyncio
async def test_provider_timeout_checkpoints_uncertain_and_writes_no_binary(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    provider = CountingProvider(
        ImageAcquisitionProviderTimeoutException("timeout")
    )
    manifest_writer = InMemoryImageAcquisitionManifestWriter()
    command, context = image_command_context
    output = await handler(
        source=source_visual_plan,
        provider=provider,
        manifest_writer=manifest_writer,
        binary_store=store(tmp_path),
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.NEEDS_USER_ACTION
    manifest = await manifest_writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status is ImageAcquisitionEntryStatus.UNCERTAIN
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.asyncio
async def test_unsupported_required_asset_fails_before_provider(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    bad_asset = source_visual_plan.visual_asset_plan.assets[0].model_copy(
        update={"asset_kind": AssetKind.VIDEO_CLIP}
    )
    bad_plan = source_visual_plan.visual_asset_plan.model_copy(
        update={"assets": (bad_asset,)}
    )
    source = source_visual_plan.model_copy(
        update={"visual_asset_plan": bad_plan}
    )
    provider = CountingProvider()
    command, context = image_command_context
    output = await handler(
        source=source,
        provider=provider,
        manifest_writer=InMemoryImageAcquisitionManifestWriter(),
        binary_store=store(tmp_path),
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert provider.calls == []


@pytest.mark.asyncio
async def test_cancelled_generation_becomes_uncertain_after_restart(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    provider = CountingProvider(asyncio.CancelledError())
    manifest_writer = InMemoryImageAcquisitionManifestWriter()
    acquired = handler(
        source=source_visual_plan,
        provider=provider,
        manifest_writer=manifest_writer,
        binary_store=store(tmp_path),
    )
    command, context = image_command_context
    with pytest.raises(asyncio.CancelledError):
        await acquired.execute(command, context)
    manifest = await manifest_writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status is ImageAcquisitionEntryStatus.UNCERTAIN
    restarted = await acquired.execute(command, context)
    assert restarted.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert provider.calls == ["asset-s001-q001-v001"]
    manifest = await manifest_writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.entries[0].status is ImageAcquisitionEntryStatus.UNCERTAIN
