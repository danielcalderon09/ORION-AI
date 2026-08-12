"""Offline production-runtime proof for opt-in hybrid visual strategies."""

from __future__ import annotations

import asyncio
import shutil
from decimal import Decimal

import pytest

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.composition import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionJobStatus
from backend.src.production.image_acquisition import (
    deserialize_hybrid_acquisition_manifest,
)
from backend.src.production.infrastructure.persistence.models import ProductionBase
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.local_mvp import LocalMvpApplication, LocalMvpRequest
from backend.src.production.rendering.image_motion import LocalHybridImageMotionRenderer
from backend.src.production.video_clip_generation.hybrid_generation import (
    deserialize_hybrid_video_manifest,
)


def _settings(
    tmp_path,
    *,
    strategy: str,
    maximum_total: str = "3.00",
    video_provider: str = "simulated",
    video_billable: bool = False,
) -> Settings:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("hybrid local E2E requires FFmpeg and FFprobe")
    return Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "hybrid.db"),
        ORION_PRODUCTION_WORKER_ENABLED=False,
        ORION_VISUAL_STRATEGY=strategy,
        ORION_IMAGE_ACQUISITION_MAX_REQUESTS_PER_JOB=20,
        ORION_IMAGE_ACQUISITION_MAX_ESTIMATED_COST_USD=Decimal("1.00"),
        ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB=20,
        ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD=Decimal("0.30"),
        ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD=Decimal("2.50"),
        ORION_VIDEO_CLIP_GENERATION_PROVIDER=video_provider,
        ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS=video_billable,
        ORION_MAX_TOTAL_VISUAL_COST_USD=Decimal(maximum_total),
        ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH=ffmpeg,
        ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH=ffprobe,
        ORION_RENDERER="ffmpeg",
        ORION_FFMPEG_PATH=ffmpeg,
        ORION_FFPROBE_PATH=ffprobe,
        ORION_RENDER_VIDEO_PRESET="ultrafast",
        ORION_RENDER_VIDEO_CRF=28,
        ORION_RENDER_PROCESS_TIMEOUT_SECONDS=240,
    )


@pytest.mark.asyncio
async def test_balanced_runtime_routes_only_selected_shots_and_muxes_audio(tmp_path) -> None:
    settings = _settings(tmp_path, strategy="hybrid_balanced")
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    try:
        report = await application.run(
            LocalMvpRequest(
                prompt="Crea un short sobre qué ocurriría si el Sol desapareciera.",
                target_duration_seconds=20,
                scene_count_hint=3,
                aspect_ratio="9:16",
            )
        )
        assert report.success is True, report.failure
        assert report.status is ProductionJobStatus.COMPLETED
        assert report.output is not None
        assert report.output.audio_codec == "aac"
        assert report.output.video_codec == "h264"
        assert (report.output.width, report.output.height) == (720, 1280)
        assert (
            report.output.frame_rate_numerator / report.output.frame_rate_denominator
        ) == pytest.approx(24, abs=0.01)
        assert report.output.duration_ms == pytest.approx(20_000, abs=500)
        assert report.visual_summary is not None
        assert report.cost_summary is not None
        assert report.cost_summary.total_accounted_cost_usd
        assert report.cost_summary.total_reported_cost_usd
        assert report.cost_summary.reported_cost_coverage_percent
        assert report.visual_summary.visual_strategy == "hybrid_balanced"
        assert report.visual_summary.generated_video_shots > 0
        assert report.visual_summary.generated_image_shots > 0

        page = await container.list_artifacts.execute(report.job_id)
        artifacts = tuple(item.artifact for item in page.items)
        manifest_artifact = next(
            item
            for item in artifacts
            if item.artifact_type is ArtifactType.HYBRID_VIDEO_GENERATION_MANIFEST
        )
        manifest = deserialize_hybrid_video_manifest(
            (settings.PROJECTS_DIR / manifest_artifact.relative_path).read_bytes()
        )
        assert sum(item.provider_call_count for item in manifest.entries) == (
            report.visual_summary.video_requests
        )
        assert all(item.provider_call_count == 0 for item in manifest.entries if item.visual_mode.value != "generated_video")
        assert any(
            item.artifact_type is ArtifactType.HYBRID_IMAGE_MOTION_COMPOSITION_PLAN
            for item in artifacts
        )
        assert any(item.artifact_type is ArtifactType.SUBTITLES for item in artifacts)
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_aggregate_budget_rejection_stops_before_asset_stages(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        strategy="hybrid_balanced",
        maximum_total="0.01",
    )
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    try:
        report = await application.run(
            LocalMvpRequest(
                prompt="Short simulado con presupuesto insuficiente.",
                target_duration_seconds=20,
                scene_count_hint=3,
            )
        )
        assert report.success is False
        assert report.failure is not None
        assert report.failure.error_code == "aggregate_visual_budget_rejected"
        page = await container.list_artifacts.execute(report.job_id)
        types = tuple(item.artifact.artifact_type for item in page.items)
        assert ArtifactType.SOURCE_IMAGE not in types
        assert ArtifactType.HYBRID_ASSET_ACQUISITION_MANIFEST not in types
        assert ArtifactType.HYBRID_VIDEO_GENERATION_MANIFEST not in types
    finally:
        await container.aclose()


@pytest.mark.parametrize(
    ("strategy", "expected_videos", "expected_images", "expected_seconds"),
    (
        ("hybrid_balanced", 5, 5, 30),
        ("hybrid_economy", 3, 7, 18),
    ),
)
@pytest.mark.asyncio
async def test_45_second_hybrid_runtime_is_deterministic(
    tmp_path,
    strategy: str,
    expected_videos: int,
    expected_images: int,
    expected_seconds: int,
) -> None:
    settings = _settings(tmp_path, strategy=strategy)
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    try:
        report = await application.run(
            LocalMvpRequest(
                prompt="Crea un short sobre qué ocurriría si el Sol desapareciera.",
                target_duration_seconds=45,
                scene_count_hint=5,
                aspect_ratio="9:16",
            )
        )
        assert report.success is True, report.failure
        assert report.output is not None
        assert report.output.duration_ms == pytest.approx(45_000, abs=500)
        assert report.output.audio_codec == "aac"
        assert (report.output.width, report.output.height) == (720, 1280)
        assert report.visual_summary is not None
        assert report.visual_summary.visual_shots == 10
        assert report.visual_summary.generated_video_shots == expected_videos
        assert report.visual_summary.generated_image_shots == expected_images
        assert report.visual_summary.video_requests == expected_videos
        assert report.visual_summary.image_requests == 10
        assert report.visual_summary.purchased_video_seconds == expected_seconds
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_image_only_runtime_renders_mp4_without_video_provider_calls(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(
        tmp_path,
        strategy="image_only",
        video_provider="openrouter",
        video_billable=False,
    )
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )

    async def forbid_video_provider_call(request):
        raise AssertionError(f"image_only attempted video request for {request.shot_id}")

    monkeypatch.setattr(
        container.video_clip_generation_provider,
        "generate_clip",
        forbid_video_provider_call,
    )
    try:
        report = await application.run(
            LocalMvpRequest(
                prompt="Explica qué es una API con ejemplos visuales claros.",
                target_duration_seconds=30,
                scene_count_hint=5,
                aspect_ratio="9:16",
            )
        )

        assert report.success is True, report.failure
        assert report.output is not None
        assert report.output.duration_ms == pytest.approx(30_000, abs=500)
        assert report.output.video_codec == "h264"
        assert report.output.audio_codec == "aac"
        assert (report.output.width, report.output.height) == (720, 1280)
        assert (
            report.output.frame_rate_numerator / report.output.frame_rate_denominator
        ) == pytest.approx(24, abs=0.01)

        assert report.visual_summary is not None
        visual = report.visual_summary
        assert visual.visual_strategy == "image_only"
        assert visual.visual_shots > 0
        assert visual.generated_image_shots == visual.visual_shots
        assert visual.generated_video_shots == 0
        assert visual.image_requests == visual.visual_shots
        assert visual.video_requests == 0
        assert visual.purchased_video_seconds == 0
        assert Decimal(visual.image_accounted_cost_usd) > 0
        assert Decimal(visual.estimated_visual_cost_usd) == Decimal(
            visual.image_estimated_cost_usd
        )

        assert report.cost_summary is not None
        assert Decimal(report.cost_summary.image_accounted_cost_usd) > 0
        assert Decimal(report.cost_summary.video_accounted_cost_usd) == 0

        page = await container.list_artifacts.execute(report.job_id)
        artifacts = tuple(item.artifact for item in page.items)
        acquisition_artifact = next(
            item
            for item in artifacts
            if item.artifact_type is ArtifactType.HYBRID_ASSET_ACQUISITION_MANIFEST
        )
        acquisition = deserialize_hybrid_acquisition_manifest(
            (settings.PROJECTS_DIR / acquisition_artifact.relative_path).read_bytes()
        )
        assert all(
            entry.image_requirement is not None
            and entry.image_requirement.value == "image_visual"
            for entry in acquisition.entries
        )
        assert len({entry.motion_mode for entry in acquisition.entries}) > 1

        video_artifact = next(
            item
            for item in artifacts
            if item.artifact_type is ArtifactType.HYBRID_VIDEO_GENERATION_MANIFEST
        )
        video_manifest = deserialize_hybrid_video_manifest(
            (settings.PROJECTS_DIR / video_artifact.relative_path).read_bytes()
        )
        assert all(entry.provider_call_count == 0 for entry in video_manifest.entries)
        assert any(item.artifact_type is ArtifactType.SUBTITLES for item in artifacts)
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_image_only_container_starts_with_disabled_openrouter_video(
    tmp_path,
) -> None:
    settings = _settings(
        tmp_path,
        strategy="image_only",
        video_provider="openrouter",
        video_billable=False,
    )
    container = build_production_container(settings)
    try:
        assert container.video_clip_generation_provider.__class__.__name__ == (
            "DisabledVideoClipGenerationProvider"
        )
    finally:
        await container.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ("hybrid_balanced", "image_only"))
async def test_partial_image_recovery_reuses_completed_assets(
    tmp_path, monkeypatch, strategy: str
) -> None:
    settings = _settings(tmp_path, strategy=strategy)
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    original = container.image_acquisition_provider.generate_image
    calls: list[str] = []

    async def fail_third_once(request):
        calls.append(request.visual_asset.asset_id)
        if len(calls) == 3:
            raise RuntimeError("offline image interruption")
        return await original(request)

    monkeypatch.setattr(container.image_acquisition_provider, "generate_image", fail_third_once)
    try:
        failed = await application.run(
            LocalMvpRequest(
                prompt="Short híbrido simulado con recuperación de imágenes.",
                target_duration_seconds=20,
                scene_count_hint=3,
            )
        )
        assert failed.status is ProductionJobStatus.WAITING_FOR_RETRY
        await asyncio.sleep(1.05)
        recovered = await container.recovery.recover()
        assert failed.job_id in recovered.requeued_job_ids
        completed = await application.run(LocalMvpRequest(resume_job_id=failed.job_id))
        assert completed.success is True, completed.failure
        assert calls.count(calls[0]) == 1
        assert calls.count(calls[1]) == 1
        assert calls.count(calls[2]) == 2
        assert completed.visual_summary is not None
        assert len(calls) == completed.visual_summary.image_requests + 1
        if strategy == "image_only":
            assert completed.visual_summary.video_requests == 0
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_partial_video_recovery_reuses_completed_clips(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, strategy="hybrid_balanced")
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    original = container.video_clip_generation_provider.generate_clip
    calls: list[str] = []

    async def fail_second_once(request):
        calls.append(request.visual_asset_id)
        if len(calls) == 2:
            raise RuntimeError("offline video interruption")
        return await original(request)

    monkeypatch.setattr(
        container.video_clip_generation_provider,
        "generate_clip",
        fail_second_once,
    )
    try:
        failed = await application.run(
            LocalMvpRequest(
                prompt="Short híbrido simulado con recuperación de video.",
                target_duration_seconds=20,
                scene_count_hint=3,
            )
        )
        assert failed.status is ProductionJobStatus.WAITING_FOR_RETRY
        await asyncio.sleep(1.05)
        recovered = await container.recovery.recover()
        assert failed.job_id in recovered.requeued_job_ids
        completed = await application.run(LocalMvpRequest(resume_job_id=failed.job_id))
        assert completed.success is True, completed.failure
        assert calls.count(calls[0]) == 1
        assert calls.count(calls[1]) == 2
        assert completed.visual_summary is not None
        assert len(calls) == completed.visual_summary.video_requests + 1
    finally:
        await container.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ("hybrid_balanced", "image_only"))
async def test_render_recovery_reuses_provider_outputs(
    tmp_path, monkeypatch, strategy: str
) -> None:
    original_render = LocalHybridImageMotionRenderer.render
    render_calls = 0

    async def fail_render_once(self, *, composition, execution):
        nonlocal render_calls
        render_calls += 1
        if render_calls == 1:
            raise ValueError("hybrid FFmpeg render failed")
        return await original_render(self, composition=composition, execution=execution)

    monkeypatch.setattr(LocalHybridImageMotionRenderer, "render", fail_render_once)
    settings = _settings(tmp_path, strategy=strategy)
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    original_video = container.video_clip_generation_provider.generate_clip
    video_calls = 0

    async def count_video(request):
        nonlocal video_calls
        video_calls += 1
        return await original_video(request)

    monkeypatch.setattr(container.video_clip_generation_provider, "generate_clip", count_video)
    try:
        failed = await application.run(
            LocalMvpRequest(
                prompt="Short híbrido simulado con recuperación de render.",
                target_duration_seconds=20,
                scene_count_hint=3,
            )
        )
        assert failed.status is ProductionJobStatus.WAITING_FOR_RETRY
        calls_before_recovery = video_calls
        await asyncio.sleep(1.05)
        recovered = await container.recovery.recover()
        assert failed.job_id in recovered.requeued_job_ids
        completed = await application.run(LocalMvpRequest(resume_job_id=failed.job_id))
        assert completed.success is True, completed.failure
        assert completed.visual_summary is not None
        assert video_calls == calls_before_recovery
        assert video_calls == completed.visual_summary.video_requests
        if strategy == "image_only":
            assert video_calls == 0
        assert render_calls == 2
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_recovery_strategy_drift_fails_closed_before_new_video_call(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path, strategy="hybrid_balanced")
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    application = LocalMvpApplication(
        create_job=container.create_job,
        get_job=container.get_job,
        list_artifacts=container.list_artifacts,
        list_events=container.list_events,
        worker=container.worker,
        workspace_root=settings.PROJECTS_DIR,
        configured_renderer="ffmpeg",
    )
    original = container.video_clip_generation_provider.generate_clip
    calls = 0

    async def fail_second(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("offline video interruption")
        return await original(request)

    monkeypatch.setattr(container.video_clip_generation_provider, "generate_clip", fail_second)
    try:
        failed = await application.run(
            LocalMvpRequest(
                prompt="Short híbrido simulado con drift durable.",
                target_duration_seconds=20,
                scene_count_hint=3,
            )
        )
        assert failed.status is ProductionJobStatus.WAITING_FOR_RETRY
        calls_before_recovery = calls
        page = await container.list_artifacts.execute(failed.job_id)
        strategy_artifact = next(
            item.artifact
            for item in page.items
            if item.artifact.artifact_type is ArtifactType.HYBRID_VISUAL_STRATEGY_PLAN
        )
        strategy_path = settings.PROJECTS_DIR / strategy_artifact.relative_path
        content = strategy_path.read_text(encoding="utf-8")
        drifted_content = content.replace(
            '"strategy_version":"1.0.0"',
            '"strategy_version":"1.0.1"',
        )
        assert drifted_content != content
        strategy_path.write_text(drifted_content, encoding="utf-8")
        await asyncio.sleep(1.05)
        recovered = await container.recovery.recover()
        assert failed.job_id in recovered.requeued_job_ids
        drifted = await application.run(LocalMvpRequest(resume_job_id=failed.job_id))
        assert drifted.status is ProductionJobStatus.FAILED
        assert drifted.failure is not None
        assert drifted.failure.error_code == "hybrid_video_generation_failed"
        assert calls == calls_before_recovery
    finally:
        await container.aclose()
