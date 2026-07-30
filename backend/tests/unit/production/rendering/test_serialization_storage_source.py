"""Strict JSON, durable storage, and verified Phase 5H.2 input reading."""

from pathlib import Path

import pytest

from backend.src.production.domain.enums import ArtifactStatus
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import (
    RenderingConflictError,
    RenderingCorruptError,
    RenderingSourceError,
)
from backend.src.production.rendering.manifest_store import (
    LocalRenderPreparationStore,
)
from backend.src.production.rendering.recovery import (
    prepare_manifest,
    validating_manifest,
)
from backend.src.production.rendering.renderers import DryRunRenderer
from backend.src.production.rendering.request_builder import (
    build_local_render_request,
)
from backend.src.production.rendering.serialization import (
    deserialize_local_render_request,
    deserialize_render_execution_manifest,
    serialize_local_render_request,
    serialize_render_execution_manifest,
)
from backend.src.production.rendering.source_reader import (
    VerifiedMediaCompositionSourceReader,
)
from backend.tests.unit.production.rendering.conftest import (
    NOW,
    StaticArtifactInventory,
    make_render_command_context,
    make_verified_source,
    write_verified_source,
)


def _store(tmp_path: Path) -> LocalRenderPreparationStore:
    return LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )


def test_serialization_is_canonical_and_rejects_corruption() -> None:
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    content = serialize_local_render_request(request)
    assert content == serialize_local_render_request(request)
    assert deserialize_local_render_request(content) == request
    duplicate = content.replace(b'{"aspect_ratio"', b'{"schema_version":"1.0.0","aspect_ratio"', 1)
    with pytest.raises(RenderingCorruptError):
        deserialize_local_render_request(duplicate)
    with pytest.raises(RenderingCorruptError):
        deserialize_local_render_request(b'{"value":NaN}')
    drifted = content.replace(request.request_fingerprint.encode(), b"0" * 64, 1)
    with pytest.raises(RenderingCorruptError, match="fingerprint"):
        deserialize_local_render_request(drifted)


@pytest.mark.asyncio
async def test_store_is_atomic_idempotent_bounded_and_cas_safe(
    tmp_path: Path,
) -> None:
    _, context = make_render_command_context()
    store = _store(tmp_path)
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    first = await store.write_request(context=context, request=request)
    second = await store.write_request(context=context, request=request)
    assert first == second
    assert await store.read_request(context=context) == request
    manifest = prepare_manifest(
        request=request,
        attempt_number=context.attempt_number,
        capabilities=DryRunRenderer().capabilities,
        now=NOW,
    )
    await store.create_manifest(context=context, manifest=manifest)
    validating = validating_manifest(manifest, now=NOW)
    await store.checkpoint_manifest(
        context=context,
        previous=manifest,
        current=validating,
    )
    with pytest.raises(RenderingConflictError, match="CAS"):
        await store.checkpoint_manifest(
            context=context,
            previous=manifest,
            current=validating,
        )
    assert not tuple(tmp_path.rglob("*.mp4"))


@pytest.mark.asyncio
async def test_store_rejects_conflicting_request_corruption_and_traversal(
    tmp_path: Path,
) -> None:
    _, context = make_render_command_context()
    store = _store(tmp_path)
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    await store.write_request(context=context, request=request)
    target = tmp_path.joinpath(
        "production",
        str(context.job_id),
        "rendering",
        f"attempt-{context.attempt_number}",
        "local-render-request.json",
    )
    target.write_bytes(b"{corrupt")
    with pytest.raises(RenderingCorruptError):
        await store.read_request(context=context)
    with pytest.raises(RenderingCorruptError):
        await store.output_exists(relative_path="../escape.mp4")


@pytest.mark.asyncio
async def test_verified_source_reader_checks_registered_bytes_and_identity(
    tmp_path: Path,
) -> None:
    source = make_verified_source()
    artifacts = write_verified_source(tmp_path, source)
    _, context = make_render_command_context()
    reader = VerifiedMediaCompositionSourceReader(
        workspace_root=tmp_path,
        inventory=StaticArtifactInventory(artifacts),
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    assert (await reader.read(context=context)).plan == source.plan

    stale_plan = source.plan_artifact.model_copy(update={"status": ArtifactStatus.INVALID})
    invalid_reader = VerifiedMediaCompositionSourceReader(
        workspace_root=tmp_path,
        inventory=StaticArtifactInventory((stale_plan, source.manifest_artifact)),
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    with pytest.raises(RenderingSourceError):
        await invalid_reader.read(context=context)


def test_manifest_serialization_rejects_unsupported_schema() -> None:
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    manifest = prepare_manifest(
        request=request,
        attempt_number=2,
        capabilities=DryRunRenderer().capabilities,
        now=NOW,
    )
    content = serialize_render_execution_manifest(manifest)
    assert deserialize_render_execution_manifest(content) == manifest
    unsupported = content.replace(b'"schema_version":"1.0.0"', b'"schema_version":"2.0.0"', 1)
    with pytest.raises(RenderingCorruptError, match="unsupported"):
        deserialize_render_execution_manifest(unsupported)
    false_media = content.replace(
        b'"media_produced":false',
        b'"media_produced":true',
        1,
    )
    with pytest.raises(RenderingCorruptError):
        deserialize_render_execution_manifest(false_media)
