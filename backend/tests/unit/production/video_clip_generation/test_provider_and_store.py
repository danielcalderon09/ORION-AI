"""Offline provider, safe probing, and specialized durable store tests."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.exceptions import (
    VideoClipConflictError,
    VideoClipIntegrityError,
    VideoClipProviderDependencyException,
)
from backend.src.production.video_clip_generation.media_probe import (
    FFprobeMediaProbe,
    VideoClipIntegrityValidator,
)
from backend.src.production.video_clip_generation.models import (
    VideoClipMetadata,
    VideoClipWriteRequest,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipProviderRequest,
)
from backend.src.production.video_clip_generation.providers.simulated_provider import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.video_store import (
    FilesystemVideoClipBinaryStore,
    video_clip_relative_path,
)
from backend.src.production.visual_asset_planning.models import VisualAssetRole
from backend.tests.unit.production.video_clip_generation.conftest import (
    COMMAND_ID,
    IMAGE_ARTIFACT_ID,
    JOB_ID,
    MANIFEST_ID,
    NOW,
    VISUAL_ASSET_ID,
    png_bytes,
)


def request(
    asset_id: str = VISUAL_ASSET_ID,
    *,
    duration: float = 1,
) -> VideoClipProviderRequest:
    content = png_bytes()
    configuration = VideoClipGenerationConfiguration(
        duration_seconds=duration,
        frame_rate=24,
    )
    return VideoClipProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        visual_asset_id=asset_id,
        source_image_artifact_id=IMAGE_ARTIFACT_ID,
        source_image_sha256=hashlib.sha256(content).hexdigest(),
        source_image_mime_type="image/png",
        source_image_size_bytes=len(content),
        source_image_width=64,
        source_image_height=64,
        source_role="hero",
        source_image_content=content,
        duration_seconds=duration,
        frame_rate=24,
        width=64,
        height=64,
        configuration=configuration,
        fingerprint=configuration.fingerprint(),
    )


def validator() -> VideoClipIntegrityValidator:
    return VideoClipIntegrityValidator(
        probe=FFprobeMediaProbe(timeout_seconds=10),
        max_video_bytes=5_000_000,
    )


@pytest.mark.asyncio
async def test_simulated_provider_produces_valid_stable_distinct_offline_mp4() -> None:
    provider = SimulatedVideoClipGenerationProvider(timeout_seconds=20)
    first = await provider.generate_clip(request())
    second = await provider.generate_clip(request())
    different = await provider.generate_clip(request("asset-s001-q002-v001"))
    assert first.clips[0].content == second.clips[0].content
    assert first.clips[0].content != different.clips[0].content
    assert first.cost_usd is None
    assert first.metadata == {
        "simulated": True,
        "deterministic": True,
        "network": False,
    }
    inspected = await validator().validate_content(
        first.clips[0].content,
        expected_width=64,
        expected_height=64,
        expected_duration_seconds=1,
        expected_frame_rate=24,
    )
    assert inspected.video_codec == "h264"
    assert inspected.frame_count == 24
    assert inspected.has_audio is False
    await provider.close()


@pytest.mark.asyncio
async def test_provider_missing_dependency_is_typed_and_close_is_safe() -> None:
    provider = SimulatedVideoClipGenerationProvider(ffmpeg_path="definitely-missing-orion-ffmpeg")
    with pytest.raises(VideoClipProviderDependencyException):
        await provider.generate_clip(request())
    await provider.close()
    await provider.close()
    with pytest.raises(VideoClipProviderDependencyException):
        await provider.generate_clip(request())


@pytest.mark.asyncio
async def test_provider_cancellation_propagates(monkeypatch) -> None:
    provider = SimulatedVideoClipGenerationProvider()

    async def cancelled(command):
        raise asyncio.CancelledError

    monkeypatch.setattr(provider, "_execute", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await provider.generate_clip(request())


@pytest.mark.asyncio
async def test_validator_rejects_renamed_images_html_and_truncated_mp4() -> None:
    for content in (png_bytes(), b"<html>no</html>", b"\x00\x00\x00\x18ftypmp4"):
        with pytest.raises(VideoClipIntegrityError):
            await validator().validate_content(
                content,
                expected_width=64,
                expected_height=64,
                expected_duration_seconds=1,
                expected_frame_rate=24,
            )


def metadata(source_sha: str) -> VideoClipMetadata:
    return VideoClipMetadata(
        source_image_manifest_artifact_id=MANIFEST_ID,
        source_image_manifest_sha256="b" * 64,
        source_image_artifact_id=IMAGE_ARTIFACT_ID,
        source_image_binary_asset_id=f"image-{VISUAL_ASSET_ID}",
        source_image_sha256=source_sha,
        source_visual_asset_id=VISUAL_ASSET_ID,
        source_scene_id="scene-001",
        source_shot_id="scene-001-shot-001",
        configuration_fingerprint=VideoClipGenerationConfiguration(
            duration_seconds=1
        ).fingerprint(),
        provider="orion-simulated",
        requested_model="simulated-video-v1",
        reported_model="simulated-video-v1",
        deterministic=True,
        attributes={"simulated": True},
    )


def write_request(source_sha: str) -> VideoClipWriteRequest:
    return VideoClipWriteRequest(
        job_id=JOB_ID,
        visual_asset_id=VISUAL_ASSET_ID,
        scene_id="scene-001",
        shot_id="scene-001-shot-001",
        role=VisualAssetRole.PRIMARY,
        expected_width=64,
        expected_height=64,
        expected_duration_seconds=1,
        expected_frame_rate=24,
        metadata=metadata(source_sha),
    )


@pytest.mark.asyncio
async def test_store_write_read_resolve_sidecar_and_idempotence(tmp_path) -> None:
    provider_request = request()
    provider = SimulatedVideoClipGenerationProvider()
    content = (await provider.generate_clip(provider_request)).clips[0].content
    store = FilesystemVideoClipBinaryStore(
        workspace_root=tmp_path,
        integrity_validator=validator(),
        max_video_bytes=5_000_000,
        clock=lambda: NOW,
    )
    asset = await store.write(
        request=write_request(provider_request.source_image_sha256),
        content=content,
    )
    repeated = await store.write(
        request=write_request(provider_request.source_image_sha256),
        content=content,
    )
    assert repeated == asset
    read = await store.read(asset=asset)
    resolved = await store.resolve(job_id=JOB_ID, visual_asset_id=VISUAL_ASSET_ID)
    assert read.content == content == resolved.content
    assert asset.sha256 == hashlib.sha256(content).hexdigest()
    assert asset.storage_path == video_clip_relative_path(
        job_id=JOB_ID, visual_asset_id=VISUAL_ASSET_ID
    )
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    assert target.is_file()
    assert Path(f"{target}.asset.json").is_file()
    assert not list(target.parent.glob("*.tmp"))
    assert not list(target.parent.glob("*.lock"))


@pytest.mark.asyncio
async def test_store_never_overwrites_conflicting_or_corrupt_content(tmp_path) -> None:
    provider_request = request()
    content = (
        (await SimulatedVideoClipGenerationProvider().generate_clip(provider_request))
        .clips[0]
        .content
    )
    store = FilesystemVideoClipBinaryStore(
        workspace_root=tmp_path,
        integrity_validator=validator(),
        max_video_bytes=5_000_000,
        clock=lambda: NOW,
    )
    asset = await store.write(
        request=write_request(provider_request.source_image_sha256),
        content=content,
    )
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    original = target.read_bytes()
    with pytest.raises(VideoClipConflictError):
        await store.write(
            request=write_request("f" * 64),
            content=content,
        )
    assert target.read_bytes() == original
    target.write_bytes(original[:-10])
    with pytest.raises(VideoClipIntegrityError):
        await store.read(asset=asset)


@pytest.mark.asyncio
async def test_store_rejects_unsafe_source_payload_before_writing(tmp_path) -> None:
    store = FilesystemVideoClipBinaryStore(
        workspace_root=tmp_path,
        integrity_validator=validator(),
        max_video_bytes=5_000_000,
    )
    with pytest.raises(VideoClipIntegrityError):
        await store.write(
            request=write_request("a" * 64),
            content=png_bytes(),
        )
    assert not (tmp_path / "production").exists()
