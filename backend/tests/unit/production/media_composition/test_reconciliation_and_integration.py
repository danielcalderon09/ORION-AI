"""Read-only reconciliation, configuration, stage, and composition guards."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.orchestration import StageRegistry
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
)
from backend.src.production.media_composition.domain.timeline import (
    build_media_composition_plan,
)
from backend.src.production.media_composition.ports import MediaCompositionSource
from backend.src.production.media_composition.reconciliation import (
    MediaCompositionReconciler,
)
from backend.src.production.media_composition.recovery import project_manifest
from backend.src.production.media_composition.storage.store import (
    LocalMediaCompositionStore,
)
from backend.tests.unit.production.media_composition.conftest import (
    NOW,
    StaticSourceReader,
    make_command_context,
)


@pytest.mark.asyncio
async def test_reconciliation_is_read_only_and_reports_healthy_state(
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
    manifest = project_manifest(
        plan=plan,
        source=composition_source,
        attempt_number=1,
        plan_relative_path=path,
        plan_sha256=digest,
        plan_size_bytes=size,
        now=NOW,
        existing=None,
    )
    await store.create_manifest(context=context, manifest=manifest)
    before = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*.json")
    }
    reconciler = MediaCompositionReconciler(
        source_reader=StaticSourceReader(composition_source),
        store=store,
        configuration=MediaCompositionConfiguration(),
    )
    result = await reconciler.reconcile(context=context)
    after = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*.json")
    }

    assert result.stage_complete
    assert result.recovery_safe
    assert not result.manual_intervention_required
    assert before == after


@pytest.mark.asyncio
async def test_reconciliation_reports_only_affected_missing_asset(
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
    validation = list(composition_source.asset_validation)
    validation[0] = validation[0].model_copy(
        update={
            "availability": CompositionAssetAvailability.MISSING,
            "actual_sha256": None,
            "issue_code": "asset_missing",
        }
    )
    source = composition_source.model_copy(update={"asset_validation": tuple(validation)})
    result = await MediaCompositionReconciler(
        source_reader=StaticSourceReader(source),
        store=store,
        configuration=MediaCompositionConfiguration(),
    ).reconcile(context=context)

    assert result.missing_asset_ids == (validation[0].asset_id,)
    assert not result.stage_complete
    assert result.recovery_safe


def test_existing_stage_order_is_preserved() -> None:
    stages = StageRegistry.PIPELINE
    assert stages.index(ProductionStage.BUILDING_TIMELINE) == (
        stages.index(ProductionStage.GENERATING_SUBTITLES) + 1
    )
    assert stages.index(ProductionStage.RENDERING_LONG_FORM) == (
        stages.index(ProductionStage.BUILDING_TIMELINE) + 1
    )


def test_settings_add_only_bounded_offline_composition_limits(
    tmp_path: Path,
) -> None:
    settings = Settings(
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
    )
    assert settings.ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES == 4_000_000
    assert settings.ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES == 4_000_000
    assert settings.ORION_SPEECH_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS is False
    assert settings.ORION_SPEECH_GENERATION_REMOTE_PROVIDER == "disabled"

    with pytest.raises(ValidationError, match="safe composition limits"):
        Settings(
            _env_file=None,
            ORION_HOME=tmp_path / "bad-home",
            MODELS_DIR=tmp_path / "bad-models",
            PROJECTS_DIR=tmp_path / "bad-projects",
            TEMP_DIR=tmp_path / "bad-temp",
            ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES=100,
        )
