"""Storage, partial invalidation, handler idempotency, and recovery."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.media_composition.application.handler import (
    MediaCompositionHandler,
)
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionAssetValidation,
    CompositionManifestStatus,
)
from backend.src.production.media_composition.domain.timeline import (
    build_media_composition_plan,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionConflictError,
    MediaCompositionCorruptError,
)
from backend.src.production.media_composition.paths import (
    media_composition_plan_relative_path,
)
from backend.src.production.media_composition.ports import MediaCompositionSource
from backend.src.production.media_composition.recovery import project_manifest
from backend.src.production.media_composition.storage.store import LocalMediaCompositionStore
from backend.tests.unit.production.media_composition.conftest import (
    NOW,
    StaticSourceReader,
    make_command_context,
)


@pytest.mark.asyncio
async def test_plan_write_is_atomic_idempotent_and_conflict_safe(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    _, context = make_command_context()
    store = LocalMediaCompositionStore(
        tmp_path,
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    plan = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    first = await store.write_plan(context=context, plan=plan)
    second = await store.write_plan(context=context, plan=plan)
    assert first == second
    assert await store.read_plan(context=context) == plan
    changed = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(fade_duration_ms=300),
    )
    with pytest.raises(MediaCompositionConflictError):
        await store.write_plan(context=context, plan=changed)


@pytest.mark.asyncio
async def test_manifest_cas_and_partial_asset_invalidation(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    _, context = make_command_context()
    store = LocalMediaCompositionStore(
        tmp_path,
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    plan = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    path, size, digest = await store.write_plan(context=context, plan=plan)
    complete = project_manifest(
        plan=plan,
        source=composition_source,
        attempt_number=1,
        plan_relative_path=path,
        plan_sha256=digest,
        plan_size_bytes=size,
        now=NOW,
        existing=None,
    )
    await store.create_manifest(context=context, manifest=complete)
    changed_validation = list(composition_source.asset_validation)
    changed_validation[0] = changed_validation[0].model_copy(
        update={
            "availability": CompositionAssetAvailability.MISSING,
            "actual_sha256": None,
            "issue_code": "asset_missing",
        }
    )
    changed_source = composition_source.model_copy(
        update={"asset_validation": tuple(changed_validation)}
    )
    invalidated = project_manifest(
        plan=plan,
        source=changed_source,
        attempt_number=1,
        plan_relative_path=path,
        plan_sha256=digest,
        plan_size_bytes=size,
        now=NOW,
        existing=complete,
    )
    await store.checkpoint_manifest(
        context=context,
        previous=complete,
        current=invalidated,
    )
    assert invalidated.status is CompositionManifestStatus.INVALIDATED
    assert invalidated.validation_summary.missing_assets == 1
    assert invalidated.asset_inventory[1:] == complete.asset_inventory[1:]
    assert await store.read_plan(context=context) == plan

    with pytest.raises(MediaCompositionConflictError, match="CAS"):
        await store.checkpoint_manifest(
            context=context,
            previous=complete,
            current=invalidated,
        )


@pytest.mark.asyncio
async def test_handler_is_idempotent_and_emits_only_json_artifacts(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    command, context = make_command_context()
    store = LocalMediaCompositionStore(
        tmp_path,
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    handler = MediaCompositionHandler(
        source_reader=StaticSourceReader(composition_source),
        store=store,
        configuration=MediaCompositionConfiguration(),
        clock=lambda: NOW,
    )
    first = await handler.execute(command, context)
    second = await handler.execute(command, context)

    assert first.result.outcome is StageOutcome.SUCCEEDED
    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert first.artifacts == second.artifacts
    assert {item.mime_type for item in first.artifacts} == {"application/json"}
    assert all(item.relative_path.endswith(".json") for item in first.artifacts)
    assert first.result.metadata["renderer_executed"] is False


@pytest.mark.asyncio
async def test_handler_keeps_plan_but_blocks_when_one_asset_disappears(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    command, context = make_command_context()
    validation = list(composition_source.asset_validation)
    validation[-1] = validation[-1].model_copy(
        update={
            "availability": CompositionAssetAvailability.MISSING,
            "actual_sha256": None,
            "issue_code": "asset_missing",
        }
    )
    source = composition_source.model_copy(update={"asset_validation": tuple(validation)})
    store = LocalMediaCompositionStore(
        tmp_path,
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    handler = MediaCompositionHandler(
        source_reader=StaticSourceReader(source),
        store=store,
        configuration=MediaCompositionConfiguration(),
        clock=lambda: NOW,
    )
    result = await handler.execute(command, context)

    assert result.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert result.artifacts == ()
    assert await store.read_plan(context=context) is not None
    manifest = await store.read_manifest(context=context)
    assert manifest is not None
    assert manifest.status is CompositionManifestStatus.INVALIDATED


@pytest.mark.asyncio
async def test_corrupt_and_hard_linked_plan_are_rejected(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    _, context = make_command_context()
    store = LocalMediaCompositionStore(
        tmp_path,
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    plan = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    await store.write_plan(context=context, plan=plan)
    target = tmp_path.joinpath(*media_composition_plan_relative_path(context).split("/"))
    link = target.with_name("hard-link.json")
    os.link(target, link)
    try:
        with pytest.raises(MediaCompositionCorruptError):
            await store.read_plan(context=context)
    finally:
        link.unlink()
    target.write_bytes(target.read_bytes().replace(b'"rec709"', b'"rec700"', 1))
    with pytest.raises(MediaCompositionCorruptError):
        await store.read_plan(context=context)


@pytest.mark.asyncio
async def test_handler_propagates_cancellation(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    command, context = make_command_context()

    class CancelledReader:
        async def read(self, *, context: object) -> MediaCompositionSource:
            del context
            raise asyncio.CancelledError

    handler = MediaCompositionHandler(
        source_reader=CancelledReader(),
        store=LocalMediaCompositionStore(
            tmp_path,
            max_plan_bytes=1_000_000,
            max_manifest_bytes=1_000_000,
        ),
        configuration=MediaCompositionConfiguration(),
        clock=lambda: NOW,
    )
    with pytest.raises(asyncio.CancelledError):
        await handler.execute(command, context)


@pytest.mark.asyncio
async def test_changed_source_fingerprint_fails_closed_without_rewriting_plan(
    tmp_path: Path,
    composition_source: MediaCompositionSource,
) -> None:
    command, context = make_command_context()
    store = LocalMediaCompositionStore(
        tmp_path,
        max_plan_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    first_handler = MediaCompositionHandler(
        source_reader=StaticSourceReader(composition_source),
        store=store,
        configuration=MediaCompositionConfiguration(),
        clock=lambda: NOW,
    )
    assert (await first_handler.execute(command, context)).result.outcome is StageOutcome.SUCCEEDED
    original = await store.read_plan(context=context)
    assert original is not None
    assets = list(composition_source.assets)
    assets[0] = assets[0].model_copy(update={"sha256": "f" * 64, "fingerprint": "e" * 64})
    changed_assets = tuple(assets)
    changed_validation = tuple(
        CompositionAssetValidation(
            asset_id=item.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=item.relative_path,
            expected_sha256=item.sha256,
            actual_sha256=item.sha256,
        )
        for item in changed_assets
    )
    changed_source = composition_source.model_copy(
        update={
            "assets": changed_assets,
            "asset_validation": changed_validation,
        }
    )
    second = await MediaCompositionHandler(
        source_reader=StaticSourceReader(changed_source),
        store=store,
        configuration=MediaCompositionConfiguration(),
        clock=lambda: NOW,
    ).execute(command, context)

    assert second.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert second.result.error_code == "media_composition_stale_plan"
    assert await store.read_plan(context=context) == original
