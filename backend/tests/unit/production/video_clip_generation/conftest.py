"""Deterministic Phase 5F.1 fixtures."""

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image

from backend.src.production.application.commands import StageCommand
from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.filesystem_store import (
    FilesystemBinaryAssetStore,
)
from backend.src.production.binary_assets.models import (
    BinaryAssetRole,
    BinaryAssetWriteRequest,
    ProductionBinaryAssetMetadata,
)
from backend.src.production.binary_assets.validators import (
    AssetHashValidator,
    AssetMimeValidator,
    AssetSizeValidator,
    BinaryAssetIntegrityValidator,
)
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
    ImageAcquisitionManifestStatus,
    ProductionImageAcquisitionEntry,
    ProductionImageAcquisitionManifest,
    summarize_entries,
)
from backend.src.production.image_acquisition.serialization import (
    serialize_image_acquisition_manifest,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.video_clip_generation.ports import (
    ImageManifestArtifactCandidate,
    InputArtifactIdentity,
    ReadImageAcquisitionManifest,
    SourceImageArtifactCandidate,
    VerifiedSourceImage,
)
from backend.src.production.visual_asset_planning.models import (
    GenerationMode,
    VisualAssetRole,
)

NOW = datetime(2026, 7, 23, 18, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000001001")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000001001")
MANIFEST_ID = UUID("30000000-0000-4000-8000-000000001001")
IMAGE_ARTIFACT_ID = UUID("40000000-0000-4000-8000-000000001001")
VISUAL_PLAN_ID = UUID("50000000-0000-4000-8000-000000001001")
VISUAL_ASSET_ID = "asset-s001-q001-v001"


@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every video test must use an in-process fake transport."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("real network access is forbidden in video tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def command_context(
    *, attempt: int = 1, input_ids: tuple[UUID, ...] = (MANIFEST_ID,)
) -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.GENERATING_VIDEO_CLIPS,
        attempt_number=attempt,
        idempotency_key=f"video:{attempt}",
        input_artifact_ids=input_ids,
        configuration_snapshot={},
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.GENERATING_VIDEO_CLIPS,
        attempt_number=attempt,
        input_artifact_ids=input_ids,
        workspace_relative_path=(f"production/{JOB_ID}/generating_video_clips/attempt-{attempt}"),
        correlation_id=JOB_ID,
    )
    return command, context


def png_bytes(color: str = "navy", *, width: int = 64, height: int = 64) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (width, height), color).save(stream, "PNG")
    return stream.getvalue()


def image_store(root: Path) -> FilesystemBinaryAssetStore:
    configuration = AssetStorageConfiguration(
        workspace=root,
        max_asset_size=1_000_000,
    )
    return FilesystemBinaryAssetStore(
        configuration=configuration,
        integrity_validator=BinaryAssetIntegrityValidator(
            mime_validator=AssetMimeValidator(configuration),
            hash_validator=AssetHashValidator(),
            size_validator=AssetSizeValidator(configuration),
        ),
        clock=lambda: NOW,
    )


class FakeImageManifestRepository:
    def __init__(
        self,
        manifest_candidate: ImageManifestArtifactCandidate,
        image_candidate: SourceImageArtifactCandidate,
    ) -> None:
        self.manifests = (manifest_candidate,)
        self.image = image_candidate
        self.input_artifacts = {
            manifest_candidate.artifact_id: InputArtifactIdentity(
                artifact_id=manifest_candidate.artifact_id,
                job_id=manifest_candidate.job_id,
                artifact_type=manifest_candidate.artifact_type,
            )
        }

    def list_candidates(self, *, job_id):
        return self.manifests

    def list_input_artifacts(self, *, artifact_ids):
        return {key: value for key, value in self.input_artifacts.items() if key in artifact_ids}

    def get_source_image(self, *, job_id, artifact_id):
        return (
            self.image if self.image is not None and artifact_id == self.image.artifact_id else None
        )


async def durable_source(root: Path, *, width: int = 64, height: int = 64):
    content = png_bytes(width=width, height=height)
    store = image_store(root)
    binary = await store.write(
        request=BinaryAssetWriteRequest(
            asset_id=f"image-{VISUAL_ASSET_ID}",
            job_id=JOB_ID,
            scene_id="scene-001",
            shot_id="scene-001-shot-001",
            asset_role=BinaryAssetRole.PRIMARY,
            mime_type="image/png",
            extension="png",
            expected_width=width,
            expected_height=height,
            metadata=ProductionBinaryAssetMetadata(
                source_visual_asset_id=VISUAL_ASSET_ID,
                source_visual_asset_plan_artifact_id=VISUAL_PLAN_ID,
                provider="orion-simulated",
                model_version="simulated-image-v1",
                deterministic=True,
                attributes={
                    "source_visual_asset_plan_sha256": "b" * 64,
                    "simulated": True,
                },
            ),
        ),
        content=content,
    )
    entry = ProductionImageAcquisitionEntry(
        visual_asset_id=VISUAL_ASSET_ID,
        scene_number=1,
        source_scene_id="scene-001",
        shot_number=1,
        source_shot_id="scene-001-shot-001",
        role=VisualAssetRole.PRIMARY,
        generation_mode=GenerationMode.TEXT_TO_IMAGE,
        status=ImageAcquisitionEntryStatus.STORED,
        binary_asset_id=binary.asset_id,
        binary_artifact_id=IMAGE_ARTIFACT_ID,
        storage_path=binary.storage_path,
        mime_type=binary.mime_type,
        extension=binary.extension,
        sha256=binary.sha256,
        size_bytes=binary.size_bytes,
        width=binary.width,
        height=binary.height,
        provider="orion-simulated",
        requested_model="simulated-image-v1",
        reported_model="simulated-image-v1",
        latency_ms=0,
        attempt_number=1,
        metadata={"simulated": True},
    )
    entries = (entry,)
    manifest = ProductionImageAcquisitionManifest(
        source_visual_asset_plan_schema_version="1.0.0",
        source_visual_asset_plan_artifact_id=VISUAL_PLAN_ID,
        source_visual_asset_plan_sha256="b" * 64,
        provider="simulated",
        requested_model="simulated-image-v1",
        reported_models=("simulated-image-v1",),
        status=ImageAcquisitionManifestStatus.COMPLETED,
        entries=entries,
        summary=summarize_entries(entries),
        metadata={"checkpointed": True},
    )
    manifest_content = serialize_image_acquisition_manifest(manifest)
    relative = f"production/{JOB_ID}/acquiring_assets/attempt-1/image-acquisition-manifest.json"
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(manifest_content)
    manifest_candidate = ImageManifestArtifactCandidate(
        artifact_id=MANIFEST_ID,
        job_id=JOB_ID,
        artifact_type=ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST,
        relative_path=relative,
        size_bytes=len(manifest_content),
        sha256=hashlib.sha256(manifest_content).hexdigest(),
        provider="simulated",
        model_version="simulated-image-v1",
        created_at=NOW,
        metadata={
            "schema_version": "1.0.0",
            "checkpointed": True,
            "unsafe_ignored": "filtered",
        },
    )
    image_candidate = SourceImageArtifactCandidate(
        artifact_id=IMAGE_ARTIFACT_ID,
        job_id=JOB_ID,
        artifact_type=ArtifactType.SOURCE_IMAGE,
        relative_path=binary.storage_path,
        mime_type=binary.mime_type,
        size_bytes=binary.size_bytes,
        sha256=binary.sha256,
        width=binary.width,
        height=binary.height,
        provider="orion-simulated",
        model_version="simulated-image-v1",
        metadata={
            "source_visual_asset_plan_artifact_id": str(VISUAL_PLAN_ID),
            "source_visual_asset_plan_sha256": "b" * 64,
            "source_visual_asset_id": VISUAL_ASSET_ID,
            "source_scene_id": "scene-001",
            "source_shot_id": "scene-001-shot-001",
            "role": VisualAssetRole.PRIMARY.value,
        },
    )
    source = ReadImageAcquisitionManifest(
        manifest=manifest,
        job_id=JOB_ID,
        artifact_id=MANIFEST_ID,
        sha256=hashlib.sha256(manifest_content).hexdigest(),
        size_bytes=len(manifest_content),
        schema_version="1.0.0",
        source_images=(
            VerifiedSourceImage(
                visual_asset_id=VISUAL_ASSET_ID,
                artifact_id=IMAGE_ARTIFACT_ID,
                binary_asset_id=binary.asset_id,
                sha256=binary.sha256,
                size_bytes=binary.size_bytes,
                mime_type=binary.mime_type,
                width=width,
                height=height,
                scene_id="scene-001",
                shot_id="scene-001-shot-001",
                scene_number=1,
                shot_number=1,
                role=VisualAssetRole.PRIMARY.value,
                content=content,
            ),
        ),
    )
    return (
        source,
        store,
        FakeImageManifestRepository(manifest_candidate, image_candidate),
    )
