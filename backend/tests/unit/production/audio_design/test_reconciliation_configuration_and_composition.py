from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.orchestration.stage_registry import (
    StageRegistry,
)
from backend.src.production.audio_design.exceptions import (
    AudioDesignProviderClosedError,
)
from backend.src.production.audio_design.manifest_store import (
    audio_design_manifest_relative_path,
)
from backend.src.production.audio_design.reconciliation import (
    AudioDesignReconciler,
    AudioDesignReconciliationIssueKind,
)
from backend.src.production.composition.container import (
    build_production_container,
)
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.speech_generation.models import (
    SUPPORTED_SPEECH_MANIFEST_VERSIONS,
)

from .conftest import build_runtime, make_script


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        **overrides,
    )


async def _persist_manifest(runtime, root: Path) -> Path:
    relative = audio_design_manifest_relative_path(runtime.context)
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(runtime.manifest_store.contents[relative])
    return target


def _reconciler(runtime, root: Path) -> AudioDesignReconciler:
    return AudioDesignReconciler(
        workspace_root=root,
        script_reader=runtime.reader,
        music_store=runtime.music_store,
        sound_effect_store=runtime.sound_effect_store,
        configuration=runtime.configuration,
    )


@pytest.mark.asyncio
async def test_reconciliation_reports_complete_state_without_mutation(tmp_path) -> None:
    runtime = build_runtime(
        tmp_path,
        script=make_script(
            music={"enabled": True},
            scene_effects=(({"cue_type": "alert"},), ()),
        ),
    )
    await runtime.handler.execute(runtime.command, runtime.context)
    await _persist_manifest(runtime, tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    report = await _reconciler(runtime, tmp_path).reconcile(context=runtime.context)
    after = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert report.stage_complete is True
    assert report.completed_asset_count == 2
    assert report.issues == ()
    assert before == after


@pytest.mark.asyncio
async def test_reconciliation_detects_missing_orphan_and_corrupt_audio(tmp_path) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))
    await runtime.handler.execute(runtime.command, runtime.context)
    await _persist_manifest(runtime, tmp_path)
    manifest = await runtime.manifest_store.read_existing(context=runtime.context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.storage_path is not None
    target = tmp_path.joinpath(*entry.storage_path.split("/"))
    orphan = target.parent / "orphan.wav"
    orphan.write_bytes(target.read_bytes())
    target.unlink()

    missing = await _reconciler(runtime, tmp_path).reconcile(context=runtime.context)

    kinds = {issue.kind for issue in missing.issues}
    assert AudioDesignReconciliationIssueKind.MISSING_ASSET in kinds
    assert AudioDesignReconciliationIssueKind.ORPHAN_ASSET in kinds
    assert missing.stage_complete is False


@pytest.mark.asyncio
async def test_reconciliation_detects_corrupt_unsupported_and_stale_manifest(
    tmp_path,
) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))
    await runtime.handler.execute(runtime.command, runtime.context)
    target = await _persist_manifest(runtime, tmp_path)
    original = target.read_bytes()

    target.write_bytes(b"{not-json")
    corrupt = await _reconciler(runtime, tmp_path).reconcile(context=runtime.context)
    assert corrupt.issues[0].kind is AudioDesignReconciliationIssueKind.CORRUPT_MANIFEST
    assert corrupt.manual_intervention_required is True

    target.write_bytes(
        original.replace(
            b'"schema_version":"1.0.0"',
            b'"schema_version":"9.0.0"',
        )
    )
    unsupported = await _reconciler(runtime, tmp_path).reconcile(context=runtime.context)
    assert unsupported.issues[0].kind is AudioDesignReconciliationIssueKind.UNSUPPORTED_SCHEMA

    target.write_bytes(original)
    runtime.reader.source = runtime.reader.source.model_copy(update={"sha256": "f" * 64})
    stale = await _reconciler(runtime, tmp_path).reconcile(context=runtime.context)
    assert AudioDesignReconciliationIssueKind.STALE_PLAN in {issue.kind for issue in stale.issues}
    assert stale.manual_intervention_required is True


def test_configuration_defaults_are_simulated_and_offline(tmp_path) -> None:
    settings = _settings(tmp_path)

    assert settings.ORION_MUSIC_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_SOUND_EFFECT_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_AUDIO_DESIGN_SAMPLE_RATE_HZ == 24_000
    assert not hasattr(settings, "ORION_MUSIC_GENERATION_API_KEY")
    assert not hasattr(settings, "ORION_SOUND_EFFECT_GENERATION_API_KEY")
    assert not hasattr(settings, "ORION_AUDIO_DESIGN_PROVIDER_URL")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_MUSIC_GENERATION_PROVIDER", "remote"),
        ("ORION_SOUND_EFFECT_GENERATION_PROVIDER", "remote"),
        ("ORION_AUDIO_DESIGN_MIN_MUSIC_DURATION_MS", 0),
        ("ORION_AUDIO_DESIGN_MAX_MUSIC_DURATION_MS", 600_001),
        ("ORION_AUDIO_DESIGN_MAX_SOUND_EFFECT_DURATION_MS", 30_001),
        ("ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES", 1_023),
    ],
)
def test_configuration_rejects_real_providers_and_unsafe_limits(
    tmp_path,
    name,
    value,
) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, **{name: value})


@pytest.mark.asyncio
async def test_composition_wires_only_simulated_audio_design_and_closes_it(
    tmp_path,
) -> None:
    container = build_production_container(_settings(tmp_path))

    assert container.music_generation_provider.provider_id == "orion-simulated-music"
    assert container.sound_effect_generation_provider.provider_id == "orion-simulated-sound-effects"
    assert container.music_asset_store is not container.sound_effect_asset_store
    assert container.audio_design_reconciler is not None

    await container.aclose()
    with pytest.raises(AudioDesignProviderClosedError, match="closed"):
        from backend.src.production.audio_design.models import (
            MusicGenerationRequest,
            MusicMood,
        )

        await container.music_generation_provider.generate(
            MusicGenerationRequest(
                request_id="music-request-" + "a" * 24,
                requirement_id="music-" + "b" * 24,
                mood=MusicMood.NEUTRAL,
                intensity=1,
                duration_ms=1_000,
                loopable=True,
                request_fingerprint="c" * 64,
            )
        )


def test_stage_order_and_existing_speech_schema_are_unchanged() -> None:
    pipeline = StageRegistry.PIPELINE
    assert pipeline.index(ProductionStage.PREPARING_MUSIC) == (
        pipeline.index(ProductionStage.GENERATING_NARRATION) + 1
    )
    assert pipeline.index(ProductionStage.GENERATING_SUBTITLES) == (
        pipeline.index(ProductionStage.PREPARING_MUSIC) + 1
    )
    assert frozenset({"1.0.0"}) == SUPPORTED_SPEECH_MANIFEST_VERSIONS
