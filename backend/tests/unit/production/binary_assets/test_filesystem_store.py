"""Atomic write, verified read, recovery, and confinement tests."""

import hashlib
import os
import stat
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetConflictError,
    BinaryAssetHashError,
    BinaryAssetLinkError,
    BinaryAssetMetadataError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.models import (
    BinaryAssetRole,
    BinaryAssetWriteRequest,
    ProductionBinaryAssetMetadata,
    ProductionBinaryAssetReference,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement

JOB_ID = UUID("22222222-2222-4222-8222-222222222222")


def request(**overrides) -> BinaryAssetWriteRequest:
    values = {
        "asset_id": "asset-s001-q001-v001",
        "job_id": JOB_ID,
        "scene_id": "scene-001",
        "shot_id": "scene-001-shot-001",
        "asset_role": BinaryAssetRole.PRIMARY,
        "mime_type": "image/png",
        "extension": "png",
        "expected_width": 4,
        "expected_height": 3,
        "metadata": ProductionBinaryAssetMetadata(
            source_visual_asset_id="asset-s001-q001-v001",
            deterministic=True,
        ),
    }
    values.update(overrides)
    return BinaryAssetWriteRequest(**values)


@pytest.mark.asyncio
async def test_write_and_read_verify_real_metadata(
    binary_store,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    sidecar = target.with_name(f"{target.name}.asset.json")
    assert target.read_bytes() == png_bytes
    assert sidecar.exists()
    assert asset.sha256 == hashlib.sha256(png_bytes).hexdigest()
    assert asset.size_bytes == len(png_bytes)
    assert (asset.width, asset.height) == (4, 3)
    read = await binary_store.read(
        reference=ProductionBinaryAssetReference.from_asset(asset)
    )
    assert read.asset == asset
    assert read.content == png_bytes


@pytest.mark.asyncio
async def test_recovery_reuses_without_rewriting(binary_store, png_bytes, tmp_path) -> None:
    first = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*first.storage_path.split("/"))
    first_timestamp = target.stat().st_mtime_ns
    recovered = await binary_store.write(request=request(), content=png_bytes)
    assert recovered == first
    assert target.stat().st_mtime_ns == first_timestamp


@pytest.mark.asyncio
async def test_existing_incompatible_asset_is_never_overwritten(
    binary_store,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    original = target.read_bytes()
    with pytest.raises(BinaryAssetConflictError):
        await binary_store.write(
            request=request(scene_id="scene-002", shot_id="scene-002-shot-001"),
            content=png_bytes,
        )
    assert target.read_bytes() == original


@pytest.mark.asyncio
async def test_corruption_and_metadata_checksum_are_rejected(
    binary_store,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    target.write_bytes(png_bytes[:-1] + b"x")
    with pytest.raises(BinaryAssetHashError):
        await binary_store.read(
            reference=ProductionBinaryAssetReference.from_asset(asset)
        )


@pytest.mark.asyncio
async def test_duplicate_metadata_keys_are_rejected(
    binary_store,
    png_bytes,
    tmp_path,
) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    sidecar = target.with_name(f"{target.name}.asset.json")
    sidecar.write_text('{"asset_id":"a","asset_id":"b"}', encoding="utf-8")
    with pytest.raises(BinaryAssetMetadataError):
        await binary_store.read(
            reference=ProductionBinaryAssetReference.from_asset(asset)
        )


def test_workspace_confinement_rejects_traversal_and_absolute(tmp_path) -> None:
    confinement = WorkspaceConfinement(tmp_path)
    with pytest.raises(BinaryAssetPathError):
        confinement.resolve("../outside.png")
    with pytest.raises(BinaryAssetPathError):
        confinement.resolve("C:/outside.png")


def test_reference_rejects_non_contractual_paths() -> None:
    with pytest.raises(ValidationError):
        ProductionBinaryAssetReference(
            asset_id="asset-a",
            job_id=JOB_ID,
            storage_path="production/other/assets/images/asset-a.png",
            mime_type="image/png",
            extension="png",
            sha256="a" * 64,
            size_bytes=10,
            width=1,
            height=1,
        )


def test_workspace_rejects_symlink_component(tmp_path, monkeypatch) -> None:
    confinement = WorkspaceConfinement(tmp_path)
    linked = tmp_path / "linked"
    original_lstat = os.lstat

    def lstat_with_link(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(linked):
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat_with_link)
    with pytest.raises(BinaryAssetLinkError):
        confinement.resolve("linked/asset.png")


@pytest.mark.asyncio
async def test_reader_rejects_hard_link(binary_store, png_bytes, tmp_path) -> None:
    asset = await binary_store.write(request=request(), content=png_bytes)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    linked = target.with_name("hard-linked.png")
    try:
        os.link(target, linked)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")
    try:
        with pytest.raises(BinaryAssetLinkError):
            await binary_store.read(
                reference=ProductionBinaryAssetReference.from_asset(asset)
            )
    finally:
        linked.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_writer_uses_atomic_replace(
    binary_store,
    png_bytes,
    monkeypatch,
) -> None:
    calls = []
    original = os.replace

    def tracked(source, destination):
        calls.append((source, destination))
        return original(source, destination)

    monkeypatch.setattr(os, "replace", tracked)
    await binary_store.write(request=request(), content=png_bytes)
    assert len(calls) == 2
