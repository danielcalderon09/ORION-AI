"""End-to-end fake-transport OpenRouter pipeline through durable MP4 storage."""

from __future__ import annotations

import hashlib
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.handler import (
    VideoClipGenerationHandler,
)
from backend.src.production.video_clip_generation.manifest_writer import (
    LocalVideoClipManifestWriter,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipProviderRequest,
)
from backend.src.production.video_clip_generation.providers.simulated_provider import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    COMMAND_ID,
    IMAGE_ARTIFACT_ID,
    JOB_ID,
    NOW,
    VISUAL_ASSET_ID,
    command_context,
    durable_source,
)
from backend.tests.unit.production.video_clip_generation.test_openrouter_provider import (
    provider_for,
    successful_transport,
)
from backend.tests.unit.production.video_clip_generation.test_reader_and_handler import (
    FakeReader,
    video_store,
)


async def valid_remote_mp4() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (720, 720), "navy").save(stream, "PNG")
    content = stream.getvalue()
    configuration = VideoClipGenerationConfiguration(
        duration_seconds=4,
        frame_rate=24,
    )
    request = VideoClipProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        visual_asset_id=VISUAL_ASSET_ID,
        source_image_artifact_id=IMAGE_ARTIFACT_ID,
        source_image_sha256=hashlib.sha256(content).hexdigest(),
        source_image_mime_type="image/png",
        source_image_size_bytes=len(content),
        source_image_width=720,
        source_image_height=720,
        source_role="primary",
        source_image_content=content,
        duration_seconds=4,
        frame_rate=24,
        width=720,
        height=720,
        configuration=configuration,
        fingerprint=configuration.fingerprint(),
    )
    provider = SimulatedVideoClipGenerationProvider(timeout_seconds=30)
    try:
        return (await provider.generate_clip(request)).clips[0].content
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_fake_openrouter_pipeline_store_manifest_artifacts_and_restart(
    tmp_path,
) -> None:
    source, _, _ = await durable_source(tmp_path)
    mp4 = await valid_remote_mp4()
    observations: list[tuple[str, str, dict[str, object] | None]] = []
    remote_store = None
    transport = successful_transport(observations, content=mp4)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openrouter.ai",
        follow_redirects=False,
    ) as client:
        remote_provider, remote_store, _ = provider_for(client)
        configuration = VideoClipGenerationConfiguration(
            provider="openrouter",
            model="test/video-model",
            resolution="720p",
            duration_seconds=4,
            frame_rate=24,
        )
        writer = LocalVideoClipManifestWriter(
            tmp_path, max_manifest_bytes=500_000
        )
        component = VideoClipGenerationHandler(
            manifest_reader=FakeReader(source),
            provider=remote_provider,
            binary_store=video_store(tmp_path),
            manifest_writer=writer,
            configuration=configuration,
            clock=lambda: NOW,
        )
        command, context = command_context()
        output = await component.execute(command, context)
        assert output.result.outcome is StageOutcome.SUCCEEDED
        assert [item.artifact_type for item in output.artifacts] == [
            ArtifactType.SOURCE_VIDEO_CLIP,
            ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
        ]
        clip_metadata = output.artifacts[0].metadata
        assert clip_metadata["remote_job_id"] == "job-abc"
        assert clip_metadata["remote_status"] == "completed"
        assert clip_metadata["simulated"] is False
        serialized = repr(output) + repr(clip_metadata)
        assert "https://" not in serialized
        assert "or-test-key-never-real" not in serialized
        manifest_path = (
            tmp_path
            / "production"
            / str(JOB_ID)
            / "generating_video_clips"
            / "attempt-1"
            / "video-clip-generation-manifest.json"
        )
        manifest = deserialize_video_clip_manifest(manifest_path.read_bytes())
        entry = manifest.entries[0]
        assert entry.remote_provider == "openrouter"
        assert entry.remote_status is not None
        assert entry.remote_status.value == "completed"
        assert entry.prompt_sha256 is not None
        assert entry.capability_snapshot_hash is not None
        assert entry.estimated_cost_usd is not None
        request_count = len(observations)

        restarted = VideoClipGenerationHandler(
            manifest_reader=FakeReader(source),
            provider=remote_provider,
            binary_store=video_store(tmp_path),
            manifest_writer=writer,
            configuration=configuration,
            clock=lambda: NOW,
        )
        recovered = await restarted.execute(command, context)
        assert recovered.result.outcome is StageOutcome.SUCCEEDED
        assert len(observations) == request_count
        assert recovered.artifacts[0].metadata["recovered"] is True
    assert remote_store is not None
    assert len(remote_store.records) == 1
