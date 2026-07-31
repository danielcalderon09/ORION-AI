"""Conservative local FFmpeg/FFprobe resolution and version identity."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from backend.src.production.rendering.exceptions import RenderingExecutableError
from backend.src.production.rendering.process_runner import (
    ControlledMediaProcessRunner,
)

_RELEASE_VERSION = re.compile(r"^(?:n)?(?P<release>[0-9]+(?:\.[0-9]+){1,3})(?:[-_].*)?$")


@dataclass(frozen=True, slots=True)
class ResolvedMediaExecutables:
    ffmpeg: Path
    ffprobe: Path


@dataclass(frozen=True, slots=True)
class MediaExecutableVersions:
    ffmpeg: str
    ffprobe: str


class LocalMediaExecutableResolver:
    """Resolve only the two closed local media executable identities."""

    def resolve(
        self,
        *,
        ffmpeg_path: Path | None,
        ffprobe_path: Path | None,
    ) -> ResolvedMediaExecutables:
        return ResolvedMediaExecutables(
            ffmpeg=self._one(ffmpeg_path, "ffmpeg"),
            ffprobe=self._one(ffprobe_path, "ffprobe"),
        )

    @staticmethod
    def _one(configured: Path | None, identity: str) -> Path:
        candidate: Path | None
        if configured is not None:
            candidate = configured.expanduser()
        else:
            found = shutil.which(identity)
            candidate = Path(found) if found else None
        if candidate is None:
            raise RenderingExecutableError(f"configured {identity} executable is unavailable")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise RenderingExecutableError(
                f"configured {identity} executable is unavailable"
            ) from exc
        if not resolved.is_absolute() or not resolved.is_file():
            raise RenderingExecutableError(
                f"configured {identity} executable is not a regular file"
            )
        suffix = resolved.suffix.lower()
        if suffix in {".bat", ".cmd", ".ps1"}:
            raise RenderingExecutableError(f"configured {identity} script is forbidden")
        if os.name == "nt" and suffix not in {".exe", ".com"}:
            raise RenderingExecutableError(
                f"configured {identity} has an invalid executable extension"
            )
        if resolved.stem.lower() != identity:
            raise RenderingExecutableError(f"configured executable identity is not {identity}")
        return resolved


async def probe_media_executable_versions(
    runner: ControlledMediaProcessRunner,
) -> MediaExecutableVersions:
    ffmpeg = await runner.run("ffmpeg", ("-version",), timeout_seconds=30)
    ffprobe = await runner.run("ffprobe", ("-version",), timeout_seconds=30)
    if ffmpeg.return_code != 0 or ffprobe.return_code != 0:
        raise RenderingExecutableError("local media executable version probe failed")
    return MediaExecutableVersions(
        ffmpeg=_normalized_version(ffmpeg.stdout, "ffmpeg"),
        ffprobe=_normalized_version(ffprobe.stdout, "ffprobe"),
    )


def _normalized_version(content: bytes, identity: str) -> str:
    try:
        first_line = content.decode("utf-8", errors="strict").splitlines()[0]
    except (UnicodeError, IndexError) as exc:
        raise RenderingExecutableError(f"{identity} version output is invalid") from exc
    prefix = f"{identity} version "
    if not first_line.lower().startswith(prefix):
        raise RenderingExecutableError(f"executable did not identify as {identity}")
    token = first_line[len(prefix) :].split(maxsplit=1)[0]
    match = _RELEASE_VERSION.fullmatch(token)
    if match is None:
        raise RenderingExecutableError(f"{identity} version identity is invalid")
    return match.group("release")
