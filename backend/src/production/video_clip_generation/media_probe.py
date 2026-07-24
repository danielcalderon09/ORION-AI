"""Safe ffprobe adapter and provider-independent MP4 integrity validation."""

import asyncio
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipIntegrityError,
    VideoClipProviderDependencyException,
    VideoClipProviderTimeoutException,
)
from backend.src.production.video_clip_generation.subprocess_io import (
    SubprocessOutputLimitError,
    communicate_limited,
)

_STDERR_LIMIT = 4_096


@dataclass(frozen=True, slots=True)
class ProbedVideoClip:
    width: int
    height: int
    duration_seconds: float
    frame_rate: float
    frame_count: int
    video_codec: str
    audio_codec: str | None
    has_audio: bool


class FFprobeMediaProbe:
    """Inspect only controlled local paths through an argument-list subprocess."""

    def __init__(self, *, executable: str = "ffprobe", timeout_seconds: float = 15) -> None:
        if not executable.strip():
            raise ValueError("ffprobe executable must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("ffprobe timeout must be positive")
        self._executable = executable
        self._timeout = timeout_seconds

    async def inspect(self, path: Path) -> ProbedVideoClip:
        command = (
            self._executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise VideoClipProviderDependencyException(
                "configured ffprobe executable is unavailable"
            ) from exc
        try:
            stdout, stderr = await communicate_limited(
                process,
                timeout_seconds=self._timeout,
                stdout_limit=1_000_000,
                stderr_limit=65_536,
            )
        except TimeoutError as exc:
            raise VideoClipProviderTimeoutException("ffprobe timed out") from exc
        except asyncio.CancelledError:
            raise
        except SubprocessOutputLimitError as exc:
            raise VideoClipIntegrityError(
                "ffprobe output exceeds the safe limit"
            ) from exc
        if process.returncode != 0:
            _bounded_detail(stderr)
            raise VideoClipIntegrityError("ffprobe rejected video clip")
        if len(stdout) > 1_000_000:
            raise VideoClipIntegrityError("ffprobe output exceeds the safe limit")
        try:
            payload = json.loads(
                stdout.decode("utf-8", errors="strict"),
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (UnicodeError, ValueError, TypeError) as exc:
            raise VideoClipIntegrityError("ffprobe output is invalid") from exc
        return _parse_probe(payload)


class VideoClipIntegrityValidator:
    """Validate MP4 structure, streams and expected media properties."""

    def __init__(
        self,
        *,
        probe: FFprobeMediaProbe,
        max_video_bytes: int,
        duration_tolerance_seconds: float = 0.08,
        frame_rate_tolerance: float = 0.01,
    ) -> None:
        if not 1 <= max_video_bytes <= 250_000_000:
            raise ValueError("maximum video clip size is outside safe limits")
        self._probe = probe
        self._maximum = max_video_bytes
        self._duration_tolerance = duration_tolerance_seconds
        self._fps_tolerance = frame_rate_tolerance

    async def validate_content(
        self,
        content: bytes,
        *,
        expected_width: int,
        expected_height: int,
        expected_duration_seconds: float,
        expected_frame_rate: float,
    ) -> ProbedVideoClip:
        self._validate_boundaries(content)
        descriptor, name = tempfile.mkstemp(suffix=".mp4")
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            return await self.validate_path(
                path,
                expected_width=expected_width,
                expected_height=expected_height,
                expected_duration_seconds=expected_duration_seconds,
                expected_frame_rate=expected_frame_rate,
            )
        finally:
            path.unlink(missing_ok=True)

    async def validate_path(
        self,
        path: Path,
        *,
        expected_width: int,
        expected_height: int,
        expected_duration_seconds: float,
        expected_frame_rate: float,
    ) -> ProbedVideoClip:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise VideoClipIntegrityError("video clip could not be inspected") from exc
        if not 1 <= size <= self._maximum:
            raise VideoClipIntegrityError("video clip size is outside safe limits")
        inspected = await self._probe.inspect(path)
        if (inspected.width, inspected.height) != (
            expected_width,
            expected_height,
        ):
            raise VideoClipIntegrityError("video clip dimensions differ from source")
        if inspected.video_codec not in {"h264", "avc1"}:
            raise VideoClipIntegrityError("video clip codec is not allowed")
        if inspected.has_audio or inspected.audio_codec is not None:
            raise VideoClipIntegrityError("video clip contains unexpected audio")
        if (
            abs(inspected.duration_seconds - expected_duration_seconds)
            > self._duration_tolerance
        ):
            raise VideoClipIntegrityError("video clip duration differs from configuration")
        if abs(inspected.frame_rate - expected_frame_rate) > self._fps_tolerance:
            raise VideoClipIntegrityError("video clip frame rate differs from configuration")
        expected_frames = round(expected_duration_seconds * expected_frame_rate)
        if abs(inspected.frame_count - expected_frames) > 1:
            raise VideoClipIntegrityError("video clip frame count is inconsistent")
        return inspected

    def _validate_boundaries(self, content: bytes) -> None:
        if not 1 <= len(content) <= self._maximum:
            raise VideoClipIntegrityError("video clip size is outside safe limits")
        if len(content) < 12 or content[4:8] != b"ftyp":
            raise VideoClipIntegrityError("video clip is not an MP4 container")
        if content.lstrip().startswith((b"<", b"{", b"[")):
            raise VideoClipIntegrityError("video clip payload is not binary MP4")


def _parse_probe(payload: Any) -> ProbedVideoClip:
    if not isinstance(payload, dict):
        raise VideoClipIntegrityError("ffprobe output must be an object")
    streams = payload.get("streams")
    format_data = payload.get("format")
    chapters = payload.get("chapters", [])
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise VideoClipIntegrityError("ffprobe output is incomplete")
    if chapters:
        raise VideoClipIntegrityError("video clip contains chapters")
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    unexpected = [
        item
        for item in streams
        if item.get("codec_type") not in {"video", "audio"}
    ]
    if len(videos) != 1 or audios or unexpected:
        raise VideoClipIntegrityError("video clip stream layout is not allowed")
    video = videos[0]
    disposition = video.get("disposition", {})
    if isinstance(disposition, dict) and disposition.get("attached_pic"):
        raise VideoClipIntegrityError("video clip contains an attachment")
    format_name = str(format_data.get("format_name", ""))
    if not {"mov", "mp4"}.intersection(format_name.split(",")):
        raise VideoClipIntegrityError("video clip container is not MP4")
    duration = _finite_float(video.get("duration", format_data.get("duration")))
    frame_rate = _parse_rate(video.get("avg_frame_rate"))
    frame_count_raw = video.get("nb_frames")
    frame_count = (
        int(frame_count_raw)
        if str(frame_count_raw).isdigit()
        else round(duration * frame_rate)
    )
    return ProbedVideoClip(
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        duration_seconds=duration,
        frame_rate=frame_rate,
        frame_count=frame_count,
        video_codec=str(video.get("codec_name", "")).lower(),
        audio_codec=None,
        has_audio=False,
    )


def _parse_rate(value: Any) -> float:
    text = str(value)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        divisor = float(denominator)
        if divisor == 0:
            raise VideoClipIntegrityError("video clip frame rate is invalid")
        return _finite_float(float(numerator) / divisor)
    return _finite_float(value)


def _finite_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise VideoClipIntegrityError("video clip numeric metadata is invalid") from exc
    if not math.isfinite(result) or result <= 0:
        raise VideoClipIntegrityError("video clip numeric metadata is invalid")
    return result


def _bounded_detail(stderr: bytes) -> str:
    value = stderr[-_STDERR_LIMIT:].decode("utf-8", errors="replace")
    value = " ".join(value.split())
    return value[-300:] or "no diagnostic"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
