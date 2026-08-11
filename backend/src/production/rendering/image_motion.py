"""Controlled local FFmpeg rendering for versioned hybrid visual timelines."""

from __future__ import annotations

import asyncio
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field, field_validator, model_validator

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.media_composition.domain.hybrid import (
    HybridImageMotionCompositionPlan,
    HybridImageMotionRenderResult,
    HybridVisualSegment,
    HybridVisualSourceKind,
    ImageMotionPlan,
)
from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner


class HybridFFmpegExecutionPlan(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    source_plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_acquisition_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_relative_path: str
    argument_vector: tuple[str, ...] = Field(min_length=1, max_length=20_000)
    filter_complex_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_duration_ms: int = Field(gt=0, le=3_600_000)
    expected_frame_count: int = Field(gt=0)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return validate_relative_path(value)

    def calculated_fingerprint(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"fingerprint"}))

    @model_validator(mode="after")
    def validate_plan(self) -> HybridFFmpegExecutionPlan:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("hybrid FFmpeg execution fingerprint differs")
        if "-stream_loop" in self.argument_vector:
            raise ValueError("hybrid visual execution cannot loop video streams")
        return self


def build_hybrid_ffmpeg_execution_plan(
    plan: HybridImageMotionCompositionPlan,
    *,
    output_relative_path: str,
) -> HybridFFmpegExecutionPlan:
    output = validate_relative_path(output_relative_path)
    args: list[str] = [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
    ]
    for segment in plan.segments:
        if segment.asset.source_kind is HybridVisualSourceKind.IMAGE:
            args.extend(("-loop", "1", "-framerate", str(plan.frame_rate)))
        args.extend(("-i", segment.asset.relative_path))
    filters: list[str] = []
    labels: list[str] = []
    for index, segment in enumerate(plan.segments):
        label = f"hv{index}"
        duration = (segment.timeline_end_frame - segment.timeline_start_frame) / plan.frame_rate
        if segment.asset.source_kind is HybridVisualSourceKind.IMAGE:
            if segment.motion_plan is None:
                raise ValueError("hybrid image segment lacks motion plan")
            chain = _image_filter(
                input_index=index,
                label=label,
                motion=segment.motion_plan,
                duration=duration,
                frame_rate=plan.frame_rate,
            )
        else:
            chain = _video_filter(
                input_index=index,
                label=label,
                segment=segment,
                duration=duration,
                width=plan.output_width,
                height=plan.output_height,
                frame_rate=plan.frame_rate,
            )
        filters.append(chain)
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vout]")
    filter_graph = ";".join(filters)
    args.extend(
        (
            "-filter_complex",
            filter_graph,
            "-map",
            "[vout]",
            "-an",
            "-frames:v",
            str(plan.expected_duration_frames),
            "-r",
            str(plan.frame_rate),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            "-n",
            output,
        )
    )
    argument_vector = tuple(args)
    filter_fingerprint = canonical_sha256(filter_graph)
    provisional = HybridFFmpegExecutionPlan.model_construct(
        source_plan_fingerprint=plan.fingerprint,
        source_strategy_fingerprint=plan.strategy_fingerprint,
        source_acquisition_fingerprint=plan.acquisition_fingerprint,
        output_relative_path=output,
        argument_vector=argument_vector,
        filter_complex_fingerprint=filter_fingerprint,
        expected_duration_ms=plan.expected_duration_ms,
        expected_frame_count=plan.expected_duration_frames,
        fingerprint="0" * 64,
    )
    return HybridFFmpegExecutionPlan(
        source_plan_fingerprint=plan.fingerprint,
        source_strategy_fingerprint=plan.strategy_fingerprint,
        source_acquisition_fingerprint=plan.acquisition_fingerprint,
        output_relative_path=output,
        argument_vector=argument_vector,
        filter_complex_fingerprint=filter_fingerprint,
        expected_duration_ms=plan.expected_duration_ms,
        expected_frame_count=plan.expected_duration_frames,
        fingerprint=provisional.calculated_fingerprint(),
    )


class LocalHybridImageMotionRenderer:
    """Verify immutable local sources, execute FFmpeg without shell, then probe."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        runner: ControlledMediaProcessRunner,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._confinement = WorkspaceConfinement(self._workspace_root)
        self._runner = runner

    async def render(
        self,
        *,
        composition: HybridImageMotionCompositionPlan,
        execution: HybridFFmpegExecutionPlan,
        timeout_seconds: float = 120,
    ) -> HybridImageMotionRenderResult:
        if execution.source_plan_fingerprint != composition.fingerprint:
            raise ValueError("hybrid execution plan source drifted")
        paths = await asyncio.to_thread(self._verify_sources, composition)
        output = self._resolve(execution.output_relative_path)
        if output.exists() or output.is_symlink():
            raise ValueError("hybrid render output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(output.parent)
        substitutions = {
            segment.asset.relative_path: str(paths[segment.asset.asset_id])
            for segment in composition.segments
        }
        substitutions[execution.output_relative_path] = str(output)
        arguments = tuple(substitutions.get(item, item) for item in execution.argument_vector)
        result = await self._runner.run(
            "ffmpeg",
            arguments,
            timeout_seconds=timeout_seconds,
        )
        if result.return_code != 0:
            raise ValueError("hybrid FFmpeg render failed")
        probe = await self._runner.run(
            "ffprobe",
            (
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ),
            timeout_seconds=30,
        )
        if probe.return_code != 0:
            raise ValueError("hybrid FFprobe validation failed")
        inspected = _parse_probe(probe.stdout)
        tolerance_ms = (1_000 + composition.frame_rate - 1) // composition.frame_rate
        if (
            inspected[0] != composition.output_width
            or inspected[1] != composition.output_height
            or inspected[2] != composition.frame_rate
            or abs(inspected[3] - composition.expected_duration_ms) > tolerance_ms
            or abs(inspected[4] - composition.expected_duration_frames) > 1
            or inspected[5] != "h264"
        ):
            raise ValueError("hybrid rendered media differs from durable plan")
        content = await asyncio.to_thread(output.read_bytes)
        return HybridImageMotionRenderResult(
            output_relative_path=execution.output_relative_path,
            output_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            duration_ms=inspected[3],
            frame_count=inspected[4],
            width=inspected[0],
            height=inspected[1],
            frame_rate=inspected[2],
            audio_stream_count=inspected[6],
            execution_fingerprint=execution.fingerprint,
        )

    def _verify_sources(
        self,
        plan: HybridImageMotionCompositionPlan,
    ) -> dict[str, Path]:
        results: dict[str, Path] = {}
        for segment in plan.segments:
            asset = segment.asset
            path = self._resolve(asset.relative_path)
            self._confinement.reject_unsafe_file(path)
            content = path.read_bytes()
            if (
                len(content) != asset.size_bytes
                or hashlib.sha256(content).hexdigest() != asset.sha256
            ):
                raise ValueError("hybrid visual source integrity differs")
            if asset.source_kind is HybridVisualSourceKind.IMAGE:
                try:
                    with Image.open(path) as image:
                        if image.size != (asset.width, asset.height):
                            raise ValueError("hybrid image dimensions differ")
                        image.verify()
                except (OSError, ValueError) as exc:
                    raise ValueError("hybrid image source is invalid") from exc
            results[asset.asset_id] = path
        return results

    def _resolve(self, relative_path: str) -> Path:
        target = self._workspace_root.joinpath(*validate_relative_path(relative_path).split("/"))
        self._confinement.reject_unsafe_components(target.parent)
        return target


def _image_filter(
    *,
    input_index: int,
    label: str,
    motion: ImageMotionPlan,
    duration: float,
    frame_rate: int,
) -> str:
    denominator = max(1, motion.frame_count - 1)
    start_scale = _decimal(motion.start_scale)
    delta_scale = _decimal(motion.end_scale - motion.start_scale)
    zoom = f"{start_scale}+({delta_scale})*on/{denominator}"
    x = _axis_expression(
        motion.start_x_basis_points,
        motion.end_x_basis_points,
        denominator,
        "iw-iw/zoom",
    )
    y = _axis_expression(
        motion.start_y_basis_points,
        motion.end_y_basis_points,
        denominator,
        "ih-ih/zoom",
    )
    return (
        f"[{input_index}:v]scale={motion.output_width}:{motion.output_height}:"
        "force_original_aspect_ratio=increase,"
        f"crop={motion.output_width}:{motion.output_height},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:"
        f"s={motion.output_width}x{motion.output_height}:fps={frame_rate},"
        f"trim=duration={_decimal(duration)},setpts=PTS-STARTPTS,"
        f"setsar=1,format=yuv420p[{label}]"
    )


def _video_filter(
    *,
    input_index: int,
    label: str,
    segment: HybridVisualSegment,
    duration: float,
    width: int,
    height: int,
    frame_rate: int,
) -> str:
    if segment.playback_mode != "once" or segment.loop_count != 1:
        raise ValueError("hybrid video playback must remain one-shot")
    return (
        f"[{input_index}:v]trim=start=0:duration={_decimal(duration)},"
        "setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={frame_rate},"
        f"format=yuv420p[{label}]"
    )


def _axis_expression(start: int, end: int, denominator: int, extent: str) -> str:
    return f"({extent})*({start}+({end - start})*on/{denominator})/10000"


def _parse_probe(content: bytes) -> tuple[int, int, int, int, int, str, int]:
    value = json.loads(content.decode("utf-8", errors="strict"))
    streams = value.get("streams", [])
    video = next(item for item in streams if item.get("codec_type") == "video")
    rate_text = str(video["avg_frame_rate"])
    numerator, denominator = (int(item) for item in rate_text.split("/", maxsplit=1))
    frame_rate = round(numerator / denominator)
    duration = video.get("duration") or value.get("format", {}).get("duration")
    frames = video.get("nb_read_frames") or video.get("nb_frames")
    return (
        int(video["width"]),
        int(video["height"]),
        frame_rate,
        round(float(duration) * 1_000),
        int(frames),
        str(video["codec_name"]),
        sum(item.get("codec_type") == "audio" for item in streams),
    )


def _decimal(value: Decimal | float | int) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


__all__ = [
    "HybridFFmpegExecutionPlan",
    "HybridImageMotionRenderResult",
    "LocalHybridImageMotionRenderer",
    "build_hybrid_ffmpeg_execution_plan",
]
