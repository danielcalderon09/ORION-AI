"""End-to-end fake-transport OpenRouter pipeline through durable MP4 storage."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
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
from backend.src.production.video_clip_generation.ports import VideoClipProviderRequest
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
    nine_second_two_shot_source,
)
from backend.tests.unit.production.video_clip_generation.test_openrouter_provider import (
    multi_scene_models_body,
    provider_for,
    successful_transport,
)
from backend.tests.unit.production.video_clip_generation.test_reader_and_handler import (
    FakeReader,
    video_store,
)


async def valid_remote_mp4(*, duration_seconds: int = 4) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (720, 720), "navy").save(stream, "PNG")
    content = stream.getvalue()
    configuration = VideoClipGenerationConfiguration(
        duration_seconds=duration_seconds,
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
        duration_seconds=duration_seconds,
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


@pytest.mark.asyncio
async def test_runtime_persists_and_executes_exact_nine_second_purchase_plan(
    tmp_path,
) -> None:
    source = await nine_second_two_shot_source(tmp_path)
    writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=500_000)
    command, context = command_context()
    content_by_duration = {
        6: await valid_remote_mp4(duration_seconds=6),
        4: await valid_remote_mp4(duration_seconds=4),
    }
    posts: list[int] = []
    first_frame_urls: list[str] = []
    plan_seen_before_posts: list[bool] = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos/models":
            return httpx.Response(
                200,
                json=multi_scene_models_body(),
                request=request,
            )
        if request.method == "POST" and request.url.path == "/api/v1/videos":
            checkpoint = await writer.read_existing(context=context)
            plan_seen_before_posts.append(
                checkpoint is not None
                and checkpoint.purchase_plan is not None
                and checkpoint.purchase_plan_fingerprint is not None
            )
            body = json.loads(request.content)
            duration = int(body["duration"])
            posts.append(duration)
            first_frame_urls.append(body["frame_images"][0]["image_url"]["url"])
            remote_id = f"job-{len(posts)}"
            return httpx.Response(
                202,
                json={
                    "id": remote_id,
                    "generation_id": f"generation-{len(posts)}",
                    "polling_url": f"/api/v1/videos/{remote_id}",
                    "status": "pending",
                },
                request=request,
            )
        if request.url.path.endswith("/content"):
            remote_id = request.url.path.split("/")[-2]
            duration = posts[int(remote_id[-1]) - 1]
            return httpx.Response(
                200,
                content=content_by_duration[duration],
                headers={"content-type": "video/mp4"},
                request=request,
            )
        if request.url.path.startswith("/api/v1/videos/job-"):
            remote_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": remote_id,
                    "generation_id": f"generation-{remote_id[-1]}",
                    "polling_url": f"/api/v1/videos/{remote_id}",
                    "status": "completed",
                    "usage": {"cost": 0.18 if remote_id == "job-1" else 0.12},
                },
                request=request,
            )
        raise AssertionError(f"unexpected fake request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport_handler),
        base_url="https://openrouter.ai",
        follow_redirects=False,
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=2,
            max_estimated_cost_usd=Decimal("0.20"),
            max_estimated_job_cost_usd=Decimal("0.30"),
            max_video_bytes=5_000_000,
        )
        configuration = VideoClipGenerationConfiguration(
            provider="openrouter",
            model="test/video-model",
            resolution="720p",
            duration_seconds=4,
            max_duration_seconds=10,
            frame_rate=24,
        )
        component = VideoClipGenerationHandler(
            manifest_reader=FakeReader(source),
            provider=provider,
            binary_store=video_store(tmp_path),
            manifest_writer=writer,
            configuration=configuration,
            clock=lambda: NOW,
        )
        output = await component.execute(command, context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert posts == [6, 4]
    assert len(first_frame_urls) == len(set(first_frame_urls)) == 2
    assert plan_seen_before_posts == [True, True]
    assert len(jobs.records) == 2
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    assert manifest.purchase_plan is not None
    assert manifest.purchase_plan_fingerprint == manifest.purchase_plan.fingerprint()
    scene = manifest.purchase_plan.scenes[0]
    assert scene.resolved_duration_ms == 9_000
    assert tuple(clip.usable_duration_ms for clip in scene.clips) == (6_000, 3_000)
    assert tuple(clip.provider_duration_seconds for clip in scene.clips) == (6, 4)
    assert len({clip.visual_intent_sha256 for clip in scene.clips}) == 2
    assert all(
        artifact.metadata["purchase_plan_fingerprint"]
        == manifest.purchase_plan_fingerprint
        for artifact in output.artifacts
        if artifact.artifact_type is ArtifactType.SOURCE_VIDEO_CLIP
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("per_request_max", "job_max"),
    ((Decimal("0.20"), Decimal("0.29")), (Decimal("0.17"), Decimal("0.30"))),
)
async def test_runtime_rejects_purchase_budget_before_first_post(
    tmp_path,
    per_request_max: Decimal,
    job_max: Decimal,
) -> None:
    source = await nine_second_two_shot_source(tmp_path)
    posts = 0

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.url.path == "/api/v1/videos/models":
            return httpx.Response(200, json=multi_scene_models_body(), request=request)
        if request.method == "POST" and request.url.path == "/api/v1/videos":
            posts += 1
        raise AssertionError(f"unexpected fake request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport_handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(
            client,
            max_requests_per_job=2,
            max_estimated_cost_usd=per_request_max,
            max_estimated_job_cost_usd=job_max,
        )
        writer = LocalVideoClipManifestWriter(tmp_path, max_manifest_bytes=500_000)
        component = VideoClipGenerationHandler(
            manifest_reader=FakeReader(source),
            provider=provider,
            binary_store=video_store(tmp_path),
            manifest_writer=writer,
            configuration=VideoClipGenerationConfiguration(
                provider="openrouter",
                model="test/video-model",
                resolution="720p",
                duration_seconds=4,
                max_duration_seconds=10,
                frame_rate=24,
            ),
            clock=lambda: NOW,
        )
        command, context = command_context()
        output = await component.execute(command, context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert posts == 0
