"""Strict FFprobe parsing and validation for the final local MP4."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from backend.src.production.rendering.exceptions import RenderingValidationError
from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.models import FFmpegExecutionPlan, LocalRenderRequest
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner


@dataclass(frozen=True, slots=True)
class ProbedRenderOutput:
    duration_ms: int
    duration_frames: int
    width: int
    height: int
    frame_rate_numerator: int
    frame_rate_denominator: int
    video_codec: str
    audio_codec: str | None
    pixel_format: str
    video_stream_count: int
    audio_stream_count: int
    subtitle_stream_count: int
    format_names: tuple[str, ...]
    probe_fingerprint: str


async def probe_render_output(
    *,
    runner: ControlledMediaProcessRunner,
    path: Path,
    request: LocalRenderRequest,
    plan: FFmpegExecutionPlan,
) -> ProbedRenderOutput:
    result = await runner.run(
        "ffprobe",
        (
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ),
        timeout_seconds=plan.execution_policy.probe_timeout_seconds,
    )
    if result.return_code != 0:
        raise RenderingValidationError("FFprobe rejected rendered output")
    try:
        payload = json.loads(
            result.stdout.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, TypeError, ValueError) as exc:
        raise RenderingValidationError("FFprobe output is invalid") from exc
    inspected = _parse(payload)
    _validate(inspected, request=request, plan=plan)
    return inspected


def _parse(payload: Any) -> ProbedRenderOutput:
    if not isinstance(payload, dict):
        raise RenderingValidationError("FFprobe output must be an object")
    streams = payload.get("streams")
    format_data = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise RenderingValidationError("FFprobe output is incomplete")
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    subtitles = [item for item in streams if item.get("codec_type") == "subtitle"]
    unexpected = [
        item for item in streams if item.get("codec_type") not in {"video", "audio", "subtitle"}
    ]
    if len(videos) != 1 or unexpected:
        raise RenderingValidationError("rendered output stream layout is invalid")
    video = videos[0]
    if any(
        isinstance(item.get("disposition"), dict) and item["disposition"].get("attached_pic")
        for item in videos
    ):
        raise RenderingValidationError("rendered output contains an attachment")
    numerator, denominator = _rate(video.get("avg_frame_rate"))
    duration_ms = round(_finite_float(video.get("duration", format_data.get("duration"))) * 1_000)
    audio_codec = str(audios[0].get("codec_name", "")).lower() if audios else None
    format_names = tuple(sorted(str(format_data.get("format_name", "")).split(",")))
    width = int(video.get("width", 0))
    height = int(video.get("height", 0))
    video_codec = str(video.get("codec_name", "")).lower()
    pixel_format = str(video.get("pix_fmt", "")).lower()
    normalized = {
        "audio_codec": audio_codec,
        "audio_stream_count": len(audios),
        "duration_ms": duration_ms,
        "format_names": format_names,
        "frame_rate_denominator": denominator,
        "frame_rate_numerator": numerator,
        "height": height,
        "pixel_format": pixel_format,
        "subtitle_stream_count": len(subtitles),
        "video_codec": video_codec,
        "video_stream_count": len(videos),
        "width": width,
    }
    return ProbedRenderOutput(
        duration_ms=duration_ms,
        duration_frames=round(duration_ms * numerator / denominator / 1_000),
        width=width,
        height=height,
        frame_rate_numerator=numerator,
        frame_rate_denominator=denominator,
        video_codec=video_codec,
        audio_codec=audio_codec,
        pixel_format=pixel_format,
        video_stream_count=len(videos),
        audio_stream_count=len(audios),
        subtitle_stream_count=len(subtitles),
        format_names=format_names,
        probe_fingerprint=canonical_sha256(normalized),
    )


def _validate(
    inspected: ProbedRenderOutput,
    *,
    request: LocalRenderRequest,
    plan: FFmpegExecutionPlan,
) -> None:
    if (inspected.width, inspected.height) != (request.output_width, request.output_height):
        raise RenderingValidationError("rendered output dimensions differ")
    actual_rate = inspected.frame_rate_numerator / inspected.frame_rate_denominator
    expected_rate = request.frame_rate_numerator / request.frame_rate_denominator
    if abs(actual_rate - expected_rate) > plan.execution_policy.frame_rate_tolerance:
        raise RenderingValidationError("rendered output frame rate differs")
    if abs(inspected.duration_ms - request.expected_duration_ms) > (
        plan.execution_policy.duration_tolerance_ms
    ):
        raise RenderingValidationError("rendered output duration differs")
    if inspected.video_codec not in {"h264", "avc1"}:
        raise RenderingValidationError("rendered output video codec is not H.264")
    if inspected.pixel_format != "yuv420p":
        raise RenderingValidationError("rendered output pixel format differs")
    if inspected.audio_stream_count < 1 or inspected.audio_codec != "aac":
        raise RenderingValidationError("rendered output AAC audio stream is missing")
    expected_subtitles = 1 if plan.subtitle_strategy == "mux_mov_text" else 0
    if inspected.subtitle_stream_count != expected_subtitles:
        raise RenderingValidationError("rendered output subtitle stream count differs")
    if not {"mov", "mp4"}.intersection(inspected.format_names):
        raise RenderingValidationError("rendered output container is not MP4")
    if inspected.duration_ms <= 0:
        raise RenderingValidationError("rendered output duration is invalid")


def _rate(value: Any) -> tuple[int, int]:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise RenderingValidationError("rendered output frame rate is invalid") from exc
    if rate <= 0:
        raise RenderingValidationError("rendered output frame rate is invalid")
    return rate.numerator, rate.denominator


def _finite_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RenderingValidationError("rendered output duration is invalid") from exc
    if not math.isfinite(result) or result <= 0:
        raise RenderingValidationError("rendered output duration is invalid")
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
