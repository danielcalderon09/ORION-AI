"""The only Phase 5H.4 subprocess boundary: exact binaries, no shell."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal

from backend.src.production.rendering.exceptions import (
    RenderingProcessError,
    RenderingProcessTimeoutError,
)
from backend.src.production.video_clip_generation.subprocess_io import (
    SubprocessOutputLimitError,
    communicate_limited,
)

ExecutableIdentity = Literal["ffmpeg", "ffprobe"]


@dataclass(frozen=True, slots=True)
class ControlledProcessResult:
    executable_identity: ExecutableIdentity
    return_code: int
    stdout: bytes
    stderr: bytes
    elapsed_ms: int


class ControlledMediaProcessRunner:
    """Run only resolved FFmpeg identities through create_subprocess_exec."""

    def __init__(
        self,
        *,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        stdout_limit: int = 1_000_000,
        stderr_limit: int = 1_000_000,
    ) -> None:
        self._paths = {"ffmpeg": ffmpeg_path, "ffprobe": ffprobe_path}
        if any(not value.is_absolute() for value in self._paths.values()):
            raise ValueError("controlled executable paths must be absolute")
        if not 1_024 <= stdout_limit <= 4_000_000:
            raise ValueError("controlled stdout limit is outside safe bounds")
        if not 1_024 <= stderr_limit <= 4_000_000:
            raise ValueError("controlled stderr limit is outside safe bounds")
        self._stdout_limit = stdout_limit
        self._stderr_limit = stderr_limit

    async def run(
        self,
        identity: ExecutableIdentity,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> ControlledProcessResult:
        if identity not in self._paths:
            raise RenderingProcessError("executable_not_allowed", "process is not allowlisted")
        if not arguments or any(not isinstance(item, str) for item in arguments):
            raise RenderingProcessError("invalid_arguments", "argument vector is invalid")
        executable = self._paths[identity]
        started = monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                str(executable),
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_safe_environment(),
            )
        except (FileNotFoundError, OSError) as exc:
            raise RenderingProcessError(
                "executable_unavailable",
                "controlled media executable could not start",
            ) from exc
        try:
            stdout, stderr = await communicate_limited(
                process,
                timeout_seconds=timeout_seconds,
                stdout_limit=self._stdout_limit,
                stderr_limit=self._stderr_limit,
            )
        except TimeoutError as exc:
            raise RenderingProcessTimeoutError(
                "process_timeout",
                "controlled media process timed out",
            ) from exc
        except SubprocessOutputLimitError as exc:
            raise RenderingProcessError(
                "diagnostic_limit",
                "controlled media process exceeded its diagnostic limit",
            ) from exc
        except asyncio.CancelledError:
            raise
        return ControlledProcessResult(
            executable_identity=identity,
            return_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            elapsed_ms=max(0, round((monotonic() - started) * 1_000)),
        )


def _safe_environment() -> dict[str, str]:
    environment = {"LC_ALL": "C", "LANG": "C"}
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        environment["SYSTEMROOT"] = system_root
    return environment
