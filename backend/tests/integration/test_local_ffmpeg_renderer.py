"""Real, tiny, local-only FFmpeg renderer integration."""

from __future__ import annotations

import hashlib
import math
import shutil
import struct
import wave
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.media_composition.domain.models import (
    CompositionAssetKind,
    CompositionAssetValidation,
)
from backend.src.production.render_validation.handler import FinalRenderValidationHandler
from backend.src.production.render_validation.probe import FFprobeFinalRenderProbe
from backend.src.production.render_validation.source_reader import (
    VerifiedFinalRenderSourceReader,
)
from backend.src.production.render_validation.store import (
    LocalFinalRenderValidationStore,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.executable_resolver import (
    LocalMediaExecutableResolver,
)
from backend.src.production.rendering.handler import LocalRenderPreparationHandler
from backend.src.production.rendering.manifest_store import LocalRenderPreparationStore
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner
from backend.src.production.rendering.renderers import LocalFFmpegRenderer
from backend.src.production.runtime.context import StageContext
from backend.tests.unit.production.media_composition.conftest import make_source
from backend.tests.unit.production.rendering.conftest import (
    NOW,
    StaticArtifactInventory,
    StaticVerifiedSourceReader,
    make_render_command_context,
    make_verified_source,
    write_verified_source,
)


@pytest.mark.asyncio
async def test_real_ffmpeg_render_is_probed_promoted_and_replayed(
    tmp_path: Path,
) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("real FFmpeg integration skipped: ffmpeg and ffprobe are unavailable")
    binaries = LocalMediaExecutableResolver().resolve(
        ffmpeg_path=None,
        ffprobe_path=None,
    )
    runner = ControlledMediaProcessRunner(
        ffmpeg_path=binaries.ffmpeg,
        ffprobe_path=binaries.ffprobe,
    )
    source = make_source()
    assets = []
    validations = []
    for asset in source.assets:
        target = tmp_path.joinpath(*asset.relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if asset.kind is CompositionAssetKind.VIDEO:
            result = await runner.run(
                "ffmpeg",
                (
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    (
                        "color=c=blue:s=160x90:r=24:d=2"
                        if asset.asset_id.endswith("001")
                        else "color=c=red:s=160x90:r=24:d=2"
                    ),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(target),
                ),
                timeout_seconds=30,
            )
            assert result.return_code == 0
            updated = asset.model_copy(
                update={
                    "width": 160,
                    "height": 90,
                    "frame_rate": 24,
                    "frame_count": 48,
                }
            )
        else:
            _write_wave(
                target,
                duration_ms=asset.duration_ms or 1_000,
                frequency_hz=(
                    0 if asset.kind is CompositionAssetKind.NARRATION else 220 + len(assets) * 20
                ),
            )
            updated = asset
        content = target.read_bytes()
        updated = updated.model_copy(
            update={
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
        assets.append(updated)
        validations.append(
            CompositionAssetValidation(
                asset_id=updated.asset_id,
                availability="available",
                relative_path=updated.relative_path,
                expected_sha256=updated.sha256,
                actual_sha256=updated.sha256,
            )
        )
    composition_source = source.model_copy(
        update={
            "assets": tuple(sorted(assets, key=lambda item: item.asset_id)),
            "asset_validation": tuple(sorted(validations, key=lambda item: item.asset_id)),
        }
    )
    verified = make_verified_source(composition_source=composition_source)
    write_verified_source(tmp_path, verified)
    configuration = RenderingConfiguration(
        renderer="ffmpeg",
        video_preset="ultrafast",
        video_crf=28,
        process_timeout_seconds=120,
        max_output_bytes=50_000_000,
        duration_tolerance_ms=150,
    )
    renderer = LocalFFmpegRenderer(workspace_root=tmp_path, runner=runner)
    store = LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=configuration.max_request_bytes,
        max_manifest_bytes=configuration.max_manifest_bytes,
        max_execution_plan_bytes=configuration.max_execution_plan_bytes,
    )
    handler = LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(verified),
        store=store,
        renderer=renderer,
        configuration=configuration,
        clock=lambda: NOW,
    )
    command, context = make_render_command_context()
    output = await handler.execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    video = next(
        item for item in output.artifacts if item.artifact_type is ArtifactType.LONG_FORM_RENDER
    )
    target = tmp_path.joinpath(*video.relative_path.split("/"))
    assert target.is_file()
    assert video.size_bytes == target.stat().st_size > 0
    assert video.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert video.metadata["width"] == 160
    assert video.metadata["height"] == 90
    assert video.metadata["video_codec"] == "h264"
    assert video.metadata["audio_codec"] == "aac"
    assert video.metadata["validated_by_ffprobe"] is True
    modified = target.stat().st_mtime_ns

    validation_command_id = UUID("20000000-0000-4000-8000-000000000955")
    input_ids = tuple(item.artifact_id for item in output.artifacts)
    validation_command = StageCommand(
        command_id=validation_command_id,
        job_id=command.job_id,
        stage=ProductionStage.VALIDATING_RENDER,
        attempt_number=1,
        idempotency_key="real-final-render-validation:test",
        input_artifact_ids=input_ids,
        created_at=NOW,
    )
    validation_context = StageContext(
        job_id=command.job_id,
        command_id=validation_command_id,
        stage=ProductionStage.VALIDATING_RENDER,
        attempt_number=1,
        input_artifact_ids=input_ids,
        workspace_relative_path=(f"production/{command.job_id}/validating_render/attempt-1"),
        correlation_id=command.job_id,
    )
    final_probe = FFprobeFinalRenderProbe(runner=runner)
    final_handler = FinalRenderValidationHandler(
        source_reader=VerifiedFinalRenderSourceReader(
            workspace_root=tmp_path,
            inventory=StaticArtifactInventory(
                (*output.artifacts, verified.plan_artifact, verified.manifest_artifact)
            ),
            max_json_bytes=4_000_000,
        ),
        store=LocalFinalRenderValidationStore(
            workspace_root=tmp_path,
            max_manifest_bytes=4_000_000,
        ),
        probe=final_probe,
        clock=lambda: NOW,
    )
    validation = await final_handler.execute(validation_command, validation_context)
    assert validation.result.outcome is StageOutcome.SUCCEEDED
    assert tuple(item.artifact_type for item in validation.artifacts) == (
        ArtifactType.FINAL_RENDER_VALIDATION,
    )
    validation_replay = await final_handler.execute(
        validation_command,
        validation_context,
    )
    assert validation_replay.result.outcome is StageOutcome.SUCCEEDED
    assert validation_replay.artifacts == validation.artifacts
    assert final_probe.invocation_count == 1

    replay = await handler.execute(command, context)
    assert replay.result.outcome is StageOutcome.SUCCEEDED
    assert target.stat().st_mtime_ns == modified
    assert renderer.invocation_count == 1
    print(
        "REAL_FFMPEG_MP4",
        f"size={video.size_bytes}",
        "duration_ms=4000",
        "streams=video:h264,audio:aac",
        "resolution=160x90",
        "fps=24/1",
    )


def _write_wave(path: Path, *, duration_ms: int, frequency_hz: int) -> None:
    sample_rate = 24_000
    frame_count = duration_ms * sample_rate // 1_000
    frames = bytearray()
    for index in range(frame_count):
        sample = (
            0
            if frequency_hz == 0
            else round(2_000 * math.sin(2 * math.pi * frequency_hz * index / sample_rate))
        )
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(bytes(frames))
