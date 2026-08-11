"""Real local FFmpeg coverage for deterministic hybrid image motion."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image, ImageDraw

from backend.src.production.domain.visual_strategy import VisualMode, VisualMotionMode
from backend.src.production.media_composition.domain.hybrid import (
    HybridVisualAssetReference,
    HybridVisualSegmentInput,
    HybridVisualSourceKind,
    build_hybrid_image_motion_plan,
    deserialize_hybrid_image_motion_plan,
    reconcile_hybrid_image_motion_plan,
    serialize_hybrid_image_motion_plan,
)
from backend.src.production.rendering.image_motion import (
    LocalHybridImageMotionRenderer,
    build_hybrid_ffmpeg_execution_plan,
)
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner

JOB_ID = UUID("10000000-0000-4000-8000-000000001401")


class CountingRunner:
    def __init__(self, delegate: ControlledMediaProcessRunner) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    async def run(self, identity, arguments, *, timeout_seconds):
        self.calls.append(identity)
        return await self.delegate.run(identity, arguments, timeout_seconds=timeout_seconds)


def _runner() -> ControlledMediaProcessRunner:
    return ControlledMediaProcessRunner(
        ffmpeg_path=Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        ffprobe_path=Path(r"C:\ffmpeg\bin\ffprobe.exe"),
    )


def _image(workspace: Path, name: str, *, width: int = 300, height: int = 500):
    relative = f"fixtures/{name}.png"
    path = workspace / "fixtures" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), color=(12, 45, 92))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, width - 20, height - 20), outline=(240, 180, 20), width=8)
    draw.ellipse((width // 3, height // 3, width * 2 // 3, height * 2 // 3), fill=(30, 180, 180))
    image.save(path, format="PNG")
    content = path.read_bytes()
    return HybridVisualAssetReference(
        asset_id=f"image-{name}",
        relative_path=relative,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="image/png",
        size_bytes=len(content),
        width=width,
        height=height,
        source_kind=HybridVisualSourceKind.IMAGE,
    )


async def _video(
    workspace: Path,
    name: str,
    *,
    duration_seconds: int = 8,
    width: int = 180,
    height: int = 320,
):
    relative = f"fixtures/{name}.mp4"
    path = workspace / "fixtures" / f"{name}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    result = await _runner().run(
        "ffmpeg",
        (
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x123456:s={width}x{height}:r=24:d={duration_seconds}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ),
        timeout_seconds=60,
    )
    assert result.return_code == 0
    content = path.read_bytes()
    return HybridVisualAssetReference(
        asset_id=f"video-{name}",
        relative_path=relative,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="video/mp4",
        size_bytes=len(content),
        width=width,
        height=height,
        source_kind=HybridVisualSourceKind.VIDEO,
        source_duration_ms=duration_seconds * 1_000,
    )


def _input(
    index: int,
    asset: HybridVisualAssetReference,
    *,
    duration_ms: int,
    motion: VisualMotionMode = VisualMotionMode.STATIC,
):
    image = asset.source_kind is HybridVisualSourceKind.IMAGE
    return HybridVisualSegmentInput(
        shot_id=f"scene-{index + 1:03d}-shot-001",
        visual_mode=(VisualMode.GENERATED_IMAGE if image else VisualMode.GENERATED_VIDEO),
        motion_mode=motion,
        usable_duration_ms=duration_ms,
        asset=asset,
    )


async def _render(
    workspace: Path,
    inputs: tuple[HybridVisualSegmentInput, ...],
    *,
    name: str,
    width: int,
    height: int,
):
    plan = build_hybrid_image_motion_plan(
        job_id=JOB_ID,
        strategy_fingerprint="a" * 64,
        acquisition_fingerprint="b" * 64,
        inputs=inputs,
        output_width=width,
        output_height=height,
        frame_rate=24,
    )
    execution = build_hybrid_ffmpeg_execution_plan(
        plan,
        output_relative_path=f"output/{name}.mp4",
    )
    renderer = LocalHybridImageMotionRenderer(
        workspace_root=workspace,
        runner=_runner(),
    )
    result = await renderer.render(composition=plan, execution=execution)
    return plan, execution, result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "motion",
    tuple(VisualMotionMode),
)
async def test_each_image_motion_renders_exact_four_second_h264(
    tmp_path: Path,
    motion: VisualMotionMode,
) -> None:
    asset = _image(tmp_path, motion.value)
    width, height = (720, 1280) if motion is VisualMotionMode.STATIC else (180, 320)
    plan, execution, result = await _render(
        tmp_path,
        (_input(0, asset, duration_ms=4_000, motion=motion),),
        name=motion.value,
        width=width,
        height=height,
    )

    assert result.duration_ms == pytest.approx(4_000, abs=42)
    assert result.frame_count == pytest.approx(96, abs=1)
    assert (result.width, result.height) == (width, height)
    assert result.video_codec == "h264"
    assert result.audio_stream_count == 0
    assert "-stream_loop" not in execution.argument_vector
    assert plan.segments[0].motion_plan is not None
    assert plan.segments[0].motion_plan.frame_count == 96


@pytest.mark.asyncio
async def test_hybrid_twenty_second_video_image_timeline_uses_cuts_and_once_video(
    tmp_path: Path,
) -> None:
    video = await _video(tmp_path, "twenty", duration_seconds=4)
    image = _image(tmp_path, "twenty")
    inputs = (
        _input(0, video, duration_ms=4_000),
        _input(1, image, duration_ms=4_000, motion=VisualMotionMode.ZOOM_IN),
        _input(2, video.model_copy(update={"asset_id": "video-twenty-second"}), duration_ms=4_000),
        _input(
            3,
            image.model_copy(update={"asset_id": "image-twenty-pan"}),
            duration_ms=4_000,
            motion=VisualMotionMode.PAN,
        ),
        _input(
            4,
            image.model_copy(update={"asset_id": "image-twenty-combined"}),
            duration_ms=4_000,
            motion=VisualMotionMode.PAN_AND_ZOOM,
        ),
    )
    plan, execution, result = await _render(
        tmp_path,
        inputs,
        name="hybrid-20s",
        width=180,
        height=320,
    )

    assert result.duration_ms == pytest.approx(20_000, abs=42)
    assert result.frame_count == pytest.approx(480, abs=1)
    assert [item.timeline_start_ms for item in plan.segments] == [0, 4_000, 8_000, 12_000, 16_000]
    assert [item.timeline_end_ms for item in plan.segments] == [
        4_000,
        8_000,
        12_000,
        16_000,
        20_000,
    ]
    assert sum(item.motion_plan is None for item in plan.segments) == 2
    assert sum(item.motion_plan is not None for item in plan.segments) == 3
    assert all(item.playback_mode == "once" and item.loop_count == 1 for item in plan.segments)
    assert (
        "concat=n=5:v=1:a=0"
        in execution.argument_vector[execution.argument_vector.index("-filter_complex") + 1]
    )


@pytest.mark.asyncio
async def test_balanced_reference_renders_five_video_and_five_image_segments(
    tmp_path: Path,
) -> None:
    video = await _video(tmp_path, "reference", duration_seconds=8, width=90, height=160)
    image = _image(tmp_path, "reference", width=120, height=180)
    durations = (5_000, 5_000, 5_000, 5_000, 5_000, 5_000, 5_000, 5_000, 4_000, 3_917)
    image_motions = (
        VisualMotionMode.PAN,
        VisualMotionMode.ZOOM_IN,
        VisualMotionMode.ZOOM_OUT,
        VisualMotionMode.PAN_AND_ZOOM,
        VisualMotionMode.PAN,
    )
    inputs = tuple(
        _input(
            index,
            (
                video.model_copy(update={"asset_id": f"video-reference-{index}"})
                if index % 2 == 0
                else image.model_copy(update={"asset_id": f"image-reference-{index}"})
            ),
            duration_ms=duration,
            motion=(VisualMotionMode.STATIC if index % 2 == 0 else image_motions[index // 2]),
        )
        for index, duration in enumerate(durations)
    )
    plan, _, result = await _render(
        tmp_path,
        inputs,
        name="hybrid-reference",
        width=90,
        height=160,
    )

    assert sum(item.motion_plan is None for item in plan.segments) == 5
    assert sum(item.motion_plan is not None for item in plan.segments) == 5
    assert result.duration_ms == pytest.approx(47_917, abs=42)
    assert result.frame_count == pytest.approx(1_150, abs=1)


@pytest.mark.asyncio
async def test_integrity_and_plan_drift_fail_before_ffmpeg(
    tmp_path: Path,
) -> None:
    image = _image(tmp_path, "integrity")
    plan = build_hybrid_image_motion_plan(
        job_id=JOB_ID,
        strategy_fingerprint="a" * 64,
        acquisition_fingerprint="b" * 64,
        inputs=(_input(0, image, duration_ms=4_000, motion=VisualMotionMode.PAN),),
        output_width=180,
        output_height=320,
        frame_rate=24,
    )
    assert reconcile_hybrid_image_motion_plan(plan, plan) is plan
    serialized = serialize_hybrid_image_motion_plan(plan)
    assert deserialize_hybrid_image_motion_plan(serialized) == plan
    changed = build_hybrid_image_motion_plan(
        job_id=JOB_ID,
        strategy_fingerprint="a" * 64,
        acquisition_fingerprint="c" * 64,
        inputs=(_input(0, image, duration_ms=4_000, motion=VisualMotionMode.PAN),),
        output_width=180,
        output_height=320,
        frame_rate=24,
    )
    with pytest.raises(ValueError, match="drifted"):
        reconcile_hybrid_image_motion_plan(plan, changed)

    execution = build_hybrid_ffmpeg_execution_plan(plan, output_relative_path="output/drift.mp4")
    counting = CountingRunner(_runner())
    renderer = LocalHybridImageMotionRenderer(
        workspace_root=tmp_path,
        runner=counting,  # type: ignore[arg-type]
    )
    (tmp_path / image.relative_path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        await renderer.render(composition=plan, execution=execution)
    assert counting.calls == []


def test_motion_derivation_is_reproducible_and_plan_pins_all_render_inputs(
    tmp_path: Path,
) -> None:
    image = _image(tmp_path, "deterministic")
    inputs = (_input(0, image, duration_ms=4_000, motion=VisualMotionMode.PAN_AND_ZOOM),)
    first = build_hybrid_image_motion_plan(
        job_id=JOB_ID,
        strategy_fingerprint="a" * 64,
        acquisition_fingerprint="b" * 64,
        inputs=inputs,
        output_width=180,
        output_height=320,
        frame_rate=24,
    )
    second = build_hybrid_image_motion_plan(
        job_id=JOB_ID,
        strategy_fingerprint="a" * 64,
        acquisition_fingerprint="b" * 64,
        inputs=inputs,
        output_width=180,
        output_height=320,
        frame_rate=24,
    )

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.segments[0].motion_plan == second.segments[0].motion_plan
    assert first.segments[0].asset.source_duration_ms is None
    assert first.segments[0].playback_mode == "once"
    assert first.segments[0].loop_count == 1
