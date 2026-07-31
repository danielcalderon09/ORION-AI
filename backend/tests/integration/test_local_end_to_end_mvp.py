"""Real local FFmpeg proof from a natural-language prompt."""

import hashlib
import shutil

import pytest
from sqlalchemy import func, select

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.composition import build_production_container
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionJobStatus,
)
from backend.src.production.infrastructure.persistence.models import (
    ProductionBase,
    StageCommandRecord,
)
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.local_mvp import LocalMvpApplication, LocalMvpRequest
from backend.src.production.rendering.renderers import LocalFFmpegRenderer


@pytest.mark.asyncio
async def test_real_local_e2e_produces_validated_mp4_and_resume_does_not_rerender(
    tmp_path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("real local E2E not proven: FFmpeg and FFprobe must both be available on PATH")

    workspace = tmp_path / "projects"
    settings = Settings(
        ORION_HOME=tmp_path / "orion-home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=workspace,
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "local-e2e.db"),
        ORION_PRODUCTION_AUTO_MIGRATE=False,
        ORION_PRODUCTION_WORKER_ENABLED=False,
        ORION_PLANNING_PROVIDER="simulated",
        ORION_SCRIPTING_PROVIDER="simulated",
        ORION_SCENE_PLANNING_PROVIDER="simulated",
        ORION_VISUAL_ASSET_PLANNING_PROVIDER="simulated",
        ORION_IMAGE_ACQUISITION_PROVIDER="simulated",
        ORION_VIDEO_CLIP_GENERATION_PROVIDER="simulated",
        ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH=ffmpeg,
        ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH=ffprobe,
        ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS=4,
        ORION_VIDEO_CLIP_GENERATION_FRAME_RATE=24,
        ORION_SPEECH_GENERATION_PROVIDER="simulated",
        ORION_MUSIC_GENERATION_PROVIDER="simulated",
        ORION_SOUND_EFFECT_GENERATION_PROVIDER="simulated",
        ORION_RENDERER="ffmpeg",
        ORION_FFMPEG_PATH=ffmpeg,
        ORION_FFPROBE_PATH=ffprobe,
        ORION_RENDER_VIDEO_PRESET="ultrafast",
        ORION_RENDER_VIDEO_CRF=28,
        ORION_RENDER_PROCESS_TIMEOUT_SECONDS=180,
        ORION_RENDER_PROBE_TIMEOUT_SECONDS=30,
    )
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=workspace,
        configured_renderer=settings.ORION_RENDERER,
    )
    try:
        report = await application.run(
            LocalMvpRequest(prompt="Explica en un video corto tres curiosidades sobre Marte.")
        )
        assert report.success is True, report.failure
        assert report.status is ProductionJobStatus.COMPLETED
        assert report.stages_succeeded == 12
        assert report.output is not None
        output = report.output
        path = workspace / output.workspace_relative_path
        assert path.is_file()
        content = path.read_bytes()
        assert len(content) == output.size_bytes > 0
        assert hashlib.sha256(content).hexdigest() == output.sha256
        assert output.video_codec == "h264"
        assert output.audio_codec == "aac"
        assert (output.width, output.height) == (360, 640)
        assert output.frame_rate_numerator / output.frame_rate_denominator == pytest.approx(
            24, abs=0.01
        )
        assert output.duration_ms == pytest.approx(8_000, abs=500)

        artifacts = await container.list_artifacts.execute(report.job_id)
        assert all(
            item.artifact.status is not ArtifactStatus.READY
            or (item.artifact.size_bytes is not None and item.artifact.size_bytes > 0)
            for item in artifacts.items
        )
        assert (
            sum(
                item.artifact.artifact_type is ArtifactType.LONG_FORM_RENDER
                for item in artifacts.items
            )
            == 1
        )
        assert (
            sum(
                item.artifact.artifact_type is ArtifactType.FINAL_RENDER_VALIDATION
                for item in artifacts.items
            )
            == 1
        )
        with container.engine.connect() as connection:
            unprocessed = connection.scalar(
                select(func.count(StageCommandRecord.command_id)).where(
                    StageCommandRecord.processed_at.is_(None)
                )
            )
        assert unprocessed == 0

        assert isinstance(container.local_renderer, LocalFFmpegRenderer)
        invocation_count = container.local_renderer.invocation_count
        modified_ns = path.stat().st_mtime_ns
        replay = await application.run(LocalMvpRequest(resume_job_id=report.job_id))
        assert replay.success is True
        assert replay.output == output
        assert replay.stage_iterations == 0
        assert container.local_renderer.invocation_count == invocation_count
        assert path.stat().st_mtime_ns == modified_ns

        print(
            "REAL_LOCAL_E2E "
            f"job={report.job_id} path={output.workspace_relative_path} "
            f"bytes={output.size_bytes} sha256={output.sha256} "
            f"duration_ms={output.duration_ms} resolution={output.width}x{output.height} "
            f"fps={output.frame_rate_numerator}/{output.frame_rate_denominator} "
            f"codecs={output.video_codec}/{output.audio_codec}"
        )
    finally:
        await container.aclose()
