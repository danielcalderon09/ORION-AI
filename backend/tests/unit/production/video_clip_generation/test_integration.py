"""Composition, registry, API privacy, lifecycle, and reconciliation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.api.schemas import CreateProductionJobRequest
from backend.src.production.application.orchestration import StageRegistry
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)
from backend.src.production.video_clip_generation.exceptions import (
    SourceImageMissingException,
)
from backend.src.production.video_clip_generation.reconciliation import (
    FilesystemVideoClipReconciler,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    JOB_ID,
    command_context,
    durable_source,
)
from backend.tests.unit.production.video_clip_generation.test_reader_and_handler import (
    CountingProvider,
    FakeReader,
    handler,
)


def settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
        "ORION_DATABASE_URL": sqlite_url_from_path(tmp_path / "composition.db"),
        "ORION_PROMPT_VIDEO_ENABLED": True,
        "ORION_PRODUCTION_WORKER_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_stage_runs_after_audio_first_narration_resolution() -> None:
    stages = StageRegistry.active_stages(generate_clips_after_render=False)
    index = stages.index(ProductionStage.ACQUIRING_ASSETS)
    assert stages[index + 1] is ProductionStage.GENERATING_NARRATION
    assert stages[index + 2] is ProductionStage.GENERATING_VIDEO_CLIPS
    assert (
        StageRegistry.previous_stage(
            ProductionStage.GENERATING_VIDEO_CLIPS,
            generate_clips_after_render=False,
        )
        is ProductionStage.GENERATING_NARRATION
    )


def test_composition_defaults_to_simulated_without_subprocess_startup(
    tmp_path, monkeypatch
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not run during composition")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    container = build_production_container(settings(tmp_path))
    assert type(container.video_clip_generation_provider).__name__ == (
        "SimulatedVideoClipGenerationProvider"
    )
    assert container.async_resources[0] is container.video_clip_generation_provider
    assert container.async_resources[1] is container.image_acquisition_provider
    assert container.video_clip_reconciler is not None
    container.shutdown()


@pytest.mark.parametrize(
    "overrides",
    [
        {"ORION_VIDEO_CLIP_GENERATION_PROVIDER": "remote"},
        {"ORION_VIDEO_CLIP_GENERATION_OUTPUT_FORMAT": "webm"},
        {"ORION_VIDEO_CLIP_GENERATION_CODEC": "vp9"},
        {"ORION_VIDEO_CLIP_GENERATION_FRAME_RATE": 25},
        {"ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS": 11},
        {"ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES": 300_000_000},
    ],
)
def test_private_global_configuration_is_closed(tmp_path, overrides) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, **overrides)


def test_optional_media_executable_paths_accept_empty_environment_values(
    tmp_path,
) -> None:
    configured = settings(
        tmp_path,
        ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH="",
        ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH="   ",
    )
    assert configured.ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH is None
    assert configured.ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH is None


def test_job_api_rejects_private_video_configuration() -> None:
    with pytest.raises(ValidationError):
        CreateProductionJobRequest(
            prompt="Create a safe video",
            configuration={
                "video_clip_generation": {
                    "provider": "simulated",
                    "duration_seconds": 1,
                    "ffmpeg_path": "private",
                }
            },
        )


def test_artifact_types_are_public_metadata_only() -> None:
    assert ArtifactType.SOURCE_VIDEO_CLIP.value == "source_video_clip"
    assert ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST.value == "production_video_clip_manifest"


class RegisteredPaths:
    def __init__(self, paths=()) -> None:
        self.paths = frozenset(paths)

    def list_registered_paths(self):
        return self.paths


class MissingStore:
    async def resolve(self, *, job_id, visual_asset_id):
        raise RuntimeError("missing")


class MissingSourceReader:
    async def read_for_video_clip_generation(self, *, context):
        raise RuntimeError("missing")


@pytest.mark.asyncio
async def test_reconciler_ignores_unknown_json_and_detects_orphan_pairs(
    tmp_path,
) -> None:
    unknown = tmp_path / "production" / str(JOB_ID) / "unknown" / "attempt-1"
    unknown.mkdir(parents=True)
    (unknown / "other.json").write_text("{}", encoding="utf-8")
    clips = tmp_path / "production" / str(JOB_ID) / "assets" / "video-clips"
    clips.mkdir(parents=True)
    (clips / "video-asset-s001-q001-v001.mp4").write_bytes(b"invalid")
    (clips / "video-asset-s001-q002-v001.mp4.asset.json").write_text("{}", encoding="utf-8")
    reconciler = FilesystemVideoClipReconciler(
        workspace_root=tmp_path,
        store=MissingStore(),
        source_reader=MissingSourceReader(),
        registered_reader=RegisteredPaths(),
        max_manifest_bytes=200_000,
    )
    report = await reconciler.reconcile()
    assert report.scanned == 2
    assert {issue.kind.value for issue in report.issues} == {
        "clip_without_sidecar",
        "sidecar_without_clip",
    }
    assert Path(unknown / "other.json").exists()


@pytest.mark.asyncio
async def test_reconciler_validates_clip_manifest_and_durable_source(
    tmp_path,
) -> None:
    source, _, _ = await durable_source(tmp_path)
    provider = CountingProvider()
    component = handler(tmp_path, source, provider)
    command, context = command_context()
    output = await component.execute(command, context)
    registered = RegisteredPaths(artifact.relative_path for artifact in output.artifacts)
    reconciler = FilesystemVideoClipReconciler(
        workspace_root=tmp_path,
        store=component._store,
        source_reader=FakeReader(source),
        registered_reader=registered,
        max_manifest_bytes=200_000,
    )
    report = await reconciler.reconcile()
    assert report.scanned == 3
    assert report.valid == 1
    assert report.issues == ()

    reconciler = FilesystemVideoClipReconciler(
        workspace_root=tmp_path,
        store=component._store,
        source_reader=FakeReader(error=SourceImageMissingException("source missing")),
        registered_reader=registered,
        max_manifest_bytes=200_000,
    )
    report = await reconciler.reconcile()
    assert report.valid == 1
    assert "source_mismatch" in {issue.kind.value for issue in report.issues}
