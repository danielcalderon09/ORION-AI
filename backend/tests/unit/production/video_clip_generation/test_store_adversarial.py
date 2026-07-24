"""Adversarial MP4 validation and specialized store guarantees."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from backend.src.production.binary_assets.exceptions import BinaryAssetLinkError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.video_clip_generation.exceptions import (
    VideoClipConflictError,
    VideoClipIntegrityError,
    VideoClipLinkError,
    VideoClipNotFoundError,
    VideoClipPathError,
)
from backend.src.production.video_clip_generation.media_probe import (
    ProbedVideoClip,
    VideoClipIntegrityValidator,
    _parse_probe,
)
from backend.src.production.video_clip_generation.providers.simulated_provider import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.video_store import (
    FilesystemVideoClipBinaryStore,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    JOB_ID,
    NOW,
    VISUAL_ASSET_ID,
)
from backend.tests.unit.production.video_clip_generation.test_provider_and_store import (
    request,
    validator,
    write_request,
)


class StaticProbe:
    def __init__(self, result: ProbedVideoClip) -> None:
        self.result = result

    async def inspect(self, path):
        return self.result


def fake_mp4() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    (
        {"video_codec": "vp9"},
        {"has_audio": True, "audio_codec": "aac"},
        {"width": 32},
        {"height": 32},
        {"duration_seconds": 2},
        {"frame_rate": 30},
        {"frame_count": 100},
    ),
)
async def test_integrity_validator_rejects_media_metadata_mismatch(change) -> None:
    baseline = ProbedVideoClip(
        width=64,
        height=64,
        duration_seconds=1,
        frame_rate=24,
        frame_count=24,
        video_codec="h264",
        audio_codec=None,
        has_audio=False,
    )
    component = VideoClipIntegrityValidator(
        probe=StaticProbe(replace(baseline, **change)),
        max_video_bytes=1_000_000,
    )
    with pytest.raises(VideoClipIntegrityError):
        await component.validate_content(
            fake_mp4(),
            expected_width=64,
            expected_height=64,
            expected_duration_seconds=1,
            expected_frame_rate=24,
        )


@pytest.mark.parametrize(
    "streams",
    (
        [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 64,
                "height": 64,
                "duration": "1",
                "avg_frame_rate": "24/1",
                "nb_frames": "24",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 64,
                "height": 64,
                "duration": "1",
                "avg_frame_rate": "24/1",
                "nb_frames": "24",
            },
            {"codec_type": "subtitle", "codec_name": "mov_text"},
        ],
        [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 64,
                "height": 64,
                "duration": "1",
                "avg_frame_rate": "24/1",
                "nb_frames": "24",
            },
            {"codec_type": "data"},
        ],
    ),
)
def test_probe_rejects_audio_subtitle_and_data_streams(streams) -> None:
    with pytest.raises(VideoClipIntegrityError):
        _parse_probe(
            {
                "streams": streams,
                "format": {"format_name": "mov,mp4", "duration": "1"},
                "chapters": [],
            }
        )


@pytest.mark.parametrize(
    "payload",
    (
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 64,
                    "height": 64,
                    "duration": "1",
                    "avg_frame_rate": "24/1",
                    "nb_frames": "24",
                    "disposition": {"attached_pic": 1},
                }
            ],
            "format": {"format_name": "mov,mp4"},
            "chapters": [],
        },
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 64,
                    "height": 64,
                    "duration": "1",
                    "avg_frame_rate": "24/1",
                    "nb_frames": "24",
                }
            ],
            "format": {"format_name": "mov,mp4"},
            "chapters": [{"id": 1}],
        },
    ),
)
def test_probe_rejects_attachments_and_chapters(payload) -> None:
    with pytest.raises(VideoClipIntegrityError):
        _parse_probe(payload)


async def _written_store(tmp_path):
    provider_request = request()
    content = (
        await SimulatedVideoClipGenerationProvider().generate_clip(provider_request)
    ).clips[0].content
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
    return store, asset, content


@pytest.mark.asyncio
async def test_store_rejects_missing_clip_and_sidecar(tmp_path) -> None:
    store, asset, _ = await _written_store(tmp_path)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    target.unlink()
    with pytest.raises(VideoClipNotFoundError):
        await store.resolve(job_id=JOB_ID, visual_asset_id=VISUAL_ASSET_ID)

    store, asset, _ = await _written_store(tmp_path / "sidecar")
    target = (tmp_path / "sidecar").joinpath(*asset.storage_path.split("/"))
    Path(f"{target}.asset.json").unlink()
    with pytest.raises(VideoClipNotFoundError):
        await store.resolve(job_id=JOB_ID, visual_asset_id=VISUAL_ASSET_ID)


@pytest.mark.asyncio
async def test_store_rejects_traversal_link_junction_and_hard_link(
    tmp_path,
    monkeypatch,
) -> None:
    store, asset, _ = await _written_store(tmp_path)
    with pytest.raises(VideoClipPathError):
        await store.resolve(job_id=JOB_ID, visual_asset_id="../../escape")

    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    original = WorkspaceConfinement._reject_link_or_reparse

    def reject_link(path: Path, *, allow_missing: bool) -> None:
        if path == target:
            raise BinaryAssetLinkError("simulated symlink or junction")
        original(path, allow_missing=allow_missing)

    monkeypatch.setattr(
        WorkspaceConfinement,
        "_reject_link_or_reparse",
        staticmethod(reject_link),
    )
    with pytest.raises(VideoClipLinkError):
        await store.read(asset=asset)
    monkeypatch.undo()

    hard_link = target.with_name("hard-linked.mp4")
    os.link(target, hard_link)
    with pytest.raises(VideoClipLinkError):
        await store.read(asset=asset)


@pytest.mark.asyncio
async def test_store_lock_prevents_concurrent_duplicate_write(tmp_path) -> None:
    store, asset, content = await _written_store(tmp_path)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    target.unlink()
    Path(f"{target}.asset.json").unlink()
    lock = target.with_name(f".{target.name}.lock")
    lock.write_bytes(b"locked")
    with pytest.raises(VideoClipConflictError):
        await store.write(
            request=write_request(asset.metadata.source_image_sha256),
            content=content,
        )


@pytest.mark.asyncio
async def test_store_uses_atomic_replace_and_fsync(
    tmp_path,
    monkeypatch,
) -> None:
    provider_request = request()
    content = (
        await SimulatedVideoClipGenerationProvider().generate_clip(provider_request)
    ).clips[0].content
    replace_calls = 0
    fsync_calls = 0
    original_replace = os.replace
    original_fsync = os.fsync

    def counted_replace(source, target):
        nonlocal replace_calls
        replace_calls += 1
        return original_replace(source, target)

    def counted_fsync(descriptor):
        nonlocal fsync_calls
        fsync_calls += 1
        return original_fsync(descriptor)

    monkeypatch.setattr(os, "replace", counted_replace)
    monkeypatch.setattr(os, "fsync", counted_fsync)
    store = FilesystemVideoClipBinaryStore(
        workspace_root=tmp_path,
        integrity_validator=validator(),
        max_video_bytes=5_000_000,
        clock=lambda: NOW,
    )
    await store.write(
        request=write_request(provider_request.source_image_sha256),
        content=content,
    )
    assert replace_calls == 2
    # Content validation and both durable file replacements fsync their files.
    # Directory fsync is additionally attempted where the platform supports it.
    assert fsync_calls >= 3
