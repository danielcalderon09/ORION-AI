"""Durable manifest creation, CAS checkpoints, locking, and corruption."""

import os
from pathlib import Path

import pytest

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipManifestConflictException,
    VideoClipManifestCorruptException,
)
from backend.src.production.video_clip_generation.manifest_writer import (
    InMemoryVideoClipManifestWriter,
    LocalVideoClipManifestWriter,
    video_clip_manifest_relative_path,
)
from backend.src.production.video_clip_generation.models import (
    VideoClipEntryStatus,
    replace_manifest_entry,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    command_context,
)
from backend.tests.unit.production.video_clip_generation.test_video_clip_models import (
    entry,
    manifest,
)


@pytest.mark.asyncio
async def test_in_memory_manifest_create_is_write_once_and_cas() -> None:
    writer = InMemoryVideoClipManifestWriter()
    _, context = command_context()
    initial = manifest()
    await writer.create(context=context, manifest=initial)
    with pytest.raises(VideoClipManifestConflictException):
        await writer.create(context=context, manifest=initial)
    generating = replace_manifest_entry(
        initial,
        entry(status=VideoClipEntryStatus.GENERATING),
    )
    with pytest.raises(VideoClipManifestConflictException):
        await writer.checkpoint(
            context=context,
            previous=generating,
            current=generating,
        )


@pytest.mark.asyncio
async def test_local_manifest_rejects_corruption_lock_and_excessive_size(
    tmp_path,
) -> None:
    _, context = command_context()
    writer = LocalVideoClipManifestWriter(
        tmp_path,
        max_manifest_bytes=200_000,
    )
    initial = manifest()
    await writer.create(context=context, manifest=initial)
    target = tmp_path.joinpath(*video_clip_manifest_relative_path(context).split("/"))
    target.write_bytes(b"{")
    with pytest.raises(VideoClipManifestCorruptException):
        await writer.read_existing(context=context)

    second_root = tmp_path / "locked"
    second_writer = LocalVideoClipManifestWriter(
        second_root,
        max_manifest_bytes=200_000,
    )
    second_target = second_root.joinpath(*video_clip_manifest_relative_path(context).split("/"))
    second_target.parent.mkdir(parents=True)
    lock = second_target.with_name(f".{second_target.name}.lock")
    lock.write_bytes(b"locked")
    with pytest.raises(VideoClipManifestConflictException):
        await second_writer.create(context=context, manifest=initial)

    small_writer = LocalVideoClipManifestWriter(
        tmp_path / "small",
        max_manifest_bytes=32,
    )
    with pytest.raises(VideoClipManifestConflictException):
        await small_writer.create(context=context, manifest=initial)


@pytest.mark.asyncio
async def test_local_manifest_checkpoint_uses_atomic_replace_and_fsync(
    tmp_path,
    monkeypatch,
) -> None:
    _, context = command_context()
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
    writer = LocalVideoClipManifestWriter(
        tmp_path,
        max_manifest_bytes=200_000,
    )
    initial = manifest()
    generating = replace_manifest_entry(
        initial,
        entry(status=VideoClipEntryStatus.GENERATING),
    )
    await writer.create(context=context, manifest=initial)
    await writer.checkpoint(
        context=context,
        previous=initial,
        current=generating,
    )
    assert replace_calls == 2
    # Directory fsync is best-effort on Windows, where opening a directory
    # descriptor may be unsupported; each file replacement is still fsynced.
    assert fsync_calls >= 2
    assert not list(Path(tmp_path).rglob("*.tmp"))
    assert not list(Path(tmp_path).rglob("*.lock"))


def test_manifest_path_rejects_noncontractual_stage_workspace() -> None:
    _, context = command_context()
    unsafe = context.model_copy(update={"workspace_relative_path": "production/not-contractual"})
    with pytest.raises(VideoClipManifestConflictException):
        video_clip_manifest_relative_path(unsafe)
