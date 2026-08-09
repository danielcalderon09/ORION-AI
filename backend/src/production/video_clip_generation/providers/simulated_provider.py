"""Deterministic offline still-image-to-MP4 provider."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from time import monotonic

from backend.src.production.video_clip_generation.exceptions import (
    VideoClipProviderDependencyException,
    VideoClipProviderResponseException,
    VideoClipProviderTimeoutException,
)
from backend.src.production.video_clip_generation.ports import (
    GeneratedVideoClipPayload,
    VideoClipProviderRequest,
    VideoClipProviderResponse,
)
from backend.src.production.video_clip_generation.subprocess_io import (
    SubprocessOutputLimitError,
    communicate_limited,
)

_MIME_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class SimulatedVideoClipGenerationProvider:
    """Animate only a verified source image using a closed ffmpeg command."""

    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        timeout_seconds: float = 30,
        max_output_bytes: int = 50_000_000,
    ) -> None:
        if not ffmpeg_path.strip():
            raise ValueError("ffmpeg executable must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("ffmpeg timeout must be positive")
        if not 1 <= max_output_bytes <= 250_000_000:
            raise ValueError("maximum ffmpeg output size is outside safe limits")
        self._ffmpeg = ffmpeg_path
        self._timeout = timeout_seconds
        self._maximum = max_output_bytes
        self._closed = False

    async def preflight_job(
        self,
        requests: tuple[VideoClipProviderRequest, ...],
    ) -> tuple[VideoClipProviderRequest, ...]:
        """Simulated clips support each planned duration without remote billing."""

        return requests

    async def generate_clip(self, request: VideoClipProviderRequest) -> VideoClipProviderResponse:
        if self._closed:
            raise VideoClipProviderDependencyException("video clip provider is closed")
        suffix = _MIME_EXTENSION.get(request.source_image_mime_type)
        if suffix is None:
            raise VideoClipProviderResponseException("source image MIME type is unsupported")
        started = monotonic()
        with tempfile.TemporaryDirectory(prefix="orion-video-clip-") as directory:
            root = Path(directory)
            source = root / f"source{suffix}"
            output = root / "output.mp4"
            await asyncio.to_thread(_write_private, source, request.source_image_content)
            digest = hashlib.sha256(request.visual_asset_id.encode("utf-8")).digest()
            direction = 1 if digest[0] % 2 else -1
            color = f"0x{digest[0]:02x}{digest[1]:02x}{digest[2]:02x}"
            box_size = max(8, min(request.width, request.height) // 18)
            travel = max(1, request.width - box_size)
            if direction > 0:
                x_expression = f"mod(t*{max(12, travel // 4)},{travel})"
            else:
                x_expression = f"{travel}-mod(t*{max(12, travel // 4)},{travel})"
            filter_value = (
                f"drawbox=x='{x_expression}':y={max(2, request.height - box_size - 2)}:"
                f"w={box_size}:h={box_size}:color={color}@0.35:t=fill,"
                "setsar=1"
            )
            frame_count = round(request.duration_seconds * request.frame_rate)
            pixel_format = (
                "yuv420p" if request.width % 2 == 0 and request.height % 2 == 0 else "yuv444p"
            )
            command = (
                self._ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-framerate",
                str(request.frame_rate),
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-map_metadata",
                "-1",
                "-an",
                "-vf",
                filter_value,
                "-frames:v",
                str(frame_count),
                "-r",
                str(request.frame_rate),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-pix_fmt",
                pixel_format,
                "-threads",
                "1",
                "-fflags",
                "+bitexact",
                "-flags:v",
                "+bitexact",
                "-movflags",
                "+faststart",
                "-y",
                str(output),
            )
            await self._execute(command)
            try:
                content = await asyncio.to_thread(
                    _read_limited_file,
                    output,
                    self._maximum,
                )
            except OSError as exc:
                raise VideoClipProviderResponseException(
                    "simulated provider output could not be read"
                ) from exc
            if not content:
                raise VideoClipProviderResponseException(
                    "simulated provider returned an empty clip"
                )
        latency_ms = max(0.0, (monotonic() - started) * 1000)
        request_id = hashlib.sha256(
            (
                f"{request.job_id}:{request.visual_asset_id}:"
                f"{request.fingerprint}:{request.source_image_sha256}"
            ).encode()
        ).hexdigest()[:32]
        return VideoClipProviderResponse(
            clips=(
                GeneratedVideoClipPayload(
                    content=content,
                    mime_type="video/mp4",
                    index=0,
                    provider_metadata={
                        "simulated": True,
                        "deterministic": True,
                        "animation": "deterministic_geometric_motion",
                    },
                ),
            ),
            provider="orion-simulated",
            requested_model=request.configuration.model,
            reported_model="simulated-video-v1",
            request_id=request_id,
            latency_ms=latency_ms,
            cost_usd=None,
            finish_reason="completed",
            metadata={
                "simulated": True,
                "deterministic": True,
                "network": False,
            },
        )

    async def _execute(self, command: tuple[str, ...]) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise VideoClipProviderDependencyException(
                "configured ffmpeg executable is unavailable"
            ) from exc
        try:
            await communicate_limited(
                process,
                timeout_seconds=self._timeout,
                stdout_limit=65_536,
                stderr_limit=65_536,
            )
        except TimeoutError as exc:
            raise VideoClipProviderTimeoutException("ffmpeg timed out") from exc
        except asyncio.CancelledError:
            raise
        except SubprocessOutputLimitError as exc:
            raise VideoClipProviderResponseException(
                "ffmpeg diagnostic output exceeds the safe limit"
            ) from exc
        if process.returncode != 0:
            # stderr is deliberately bounded above and not exposed: it may contain
            # private temporary paths or build environment details.
            raise VideoClipProviderResponseException("ffmpeg failed to create a clip")

    async def close(self) -> None:
        self._closed = True


def _write_private(path: Path, content: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _read_limited_file(path: Path, maximum: int) -> bytes:
    if path.stat().st_size > maximum:
        raise VideoClipProviderResponseException(
            "simulated provider output exceeds the configured limit"
        )
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise VideoClipProviderResponseException(
            "simulated provider output exceeds the configured limit"
        )
    return content
