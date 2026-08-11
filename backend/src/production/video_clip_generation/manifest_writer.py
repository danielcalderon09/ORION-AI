"""Atomic compare-and-swap checkpoints for video clip manifests."""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import Field

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext
from backend.src.production.video_clip_generation.exceptions import (
    VideoClipManifestConflictException,
    VideoClipManifestCorruptException,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipManifest,
    VideoClipManifestStatus,
    validate_manifest_transition,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
    serialize_video_clip_manifest,
)


class WrittenVideoClipManifest(ContractModel):
    relative_path: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest: ProductionVideoClipManifest


class InMemoryVideoClipManifestWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}
        self.checkpoint_count = 0

    async def read_existing(self, *, context: StageContext) -> ProductionVideoClipManifest | None:
        content = self.contents.get(video_clip_manifest_relative_path(context))
        return deserialize_video_clip_manifest(content) if content is not None else None

    async def read_latest_before(
        self, *, context: StageContext
    ) -> ProductionVideoClipManifest | None:
        for attempt in range(context.attempt_number - 1, 0, -1):
            path = (
                f"production/{context.job_id}/generating_video_clips/attempt-{attempt}/"
                "video-clip-generation-manifest.json"
            )
            content = self.contents.get(path)
            if content is not None:
                return deserialize_video_clip_manifest(content)
        return None

    async def create(self, *, context: StageContext, manifest: ProductionVideoClipManifest) -> None:
        path = video_clip_manifest_relative_path(context)
        if path in self.contents:
            raise VideoClipManifestConflictException("video clip manifest already exists")
        self.contents[path] = serialize_video_clip_manifest(manifest)
        self.checkpoint_count += 1

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None:
        _validate_transition(previous, current)
        path = video_clip_manifest_relative_path(context)
        if self.contents.get(path) != serialize_video_clip_manifest(previous):
            raise VideoClipManifestConflictException("video clip checkpoint changed concurrently")
        self.contents[path] = serialize_video_clip_manifest(current)
        self.checkpoint_count += 1

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None:
        if current.status is not VideoClipManifestStatus.COMPLETED:
            raise VideoClipManifestConflictException("final video clip manifest must be completed")
        await self.checkpoint(context=context, previous=previous, current=current)

    def written(self, *, context: StageContext) -> WrittenVideoClipManifest:
        path = video_clip_manifest_relative_path(context)
        return _written(path, self.contents[path])


class LocalVideoClipManifestWriter:
    def __init__(self, workspace_root: Path, *, max_manifest_bytes: int) -> None:
        if not 1 <= max_manifest_bytes <= 50_000_000:
            raise ValueError("maximum video clip manifest size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_manifest_bytes

    async def read_existing(self, *, context: StageContext) -> ProductionVideoClipManifest | None:
        return await asyncio.to_thread(self._read_existing_sync, context)

    async def read_latest_before(
        self, *, context: StageContext
    ) -> ProductionVideoClipManifest | None:
        return await asyncio.to_thread(self._read_latest_before_sync, context)

    async def create(self, *, context: StageContext, manifest: ProductionVideoClipManifest) -> None:
        await asyncio.to_thread(self._create_sync, context, manifest)

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, context, previous, current)

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None:
        if current.status is not VideoClipManifestStatus.COMPLETED:
            raise VideoClipManifestConflictException("final video clip manifest must be completed")
        await self.checkpoint(context=context, previous=previous, current=current)

    async def written(self, *, context: StageContext) -> WrittenVideoClipManifest:
        return await asyncio.to_thread(self._written_sync, context)

    def _read_existing_sync(self, context: StageContext) -> ProductionVideoClipManifest | None:
        target = self._target(context)
        if not target.exists():
            return None
        return self._read(target)

    def _read_latest_before_sync(
        self, context: StageContext
    ) -> ProductionVideoClipManifest | None:
        for attempt in range(context.attempt_number - 1, 0, -1):
            relative = (
                f"production/{context.job_id}/generating_video_clips/attempt-{attempt}/"
                "video-clip-generation-manifest.json"
            )
            target = self._confinement.resolve(relative)
            if target.exists():
                return self._read(target)
        return None

    def _create_sync(self, context: StageContext, manifest: ProductionVideoClipManifest) -> None:
        target = self._target(context)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target)
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise VideoClipManifestConflictException(
                "video clip manifest directory is unsafe"
            ) from exc
        with self._exclusive_lock(target):
            if target.exists() or target.is_symlink():
                raise VideoClipManifestConflictException("video clip manifest already exists")
            self._replace(target, serialize_video_clip_manifest(manifest))

    def _checkpoint_sync(
        self,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None:
        _validate_transition(previous, current)
        target = self._target(context)
        with self._exclusive_lock(target):
            if self._read(target) != previous:
                raise VideoClipManifestConflictException(
                    "video clip checkpoint changed concurrently"
                )
            self._replace(target, serialize_video_clip_manifest(current))

    def _written_sync(self, context: StageContext) -> WrittenVideoClipManifest:
        target = self._target(context)
        return _written(video_clip_manifest_relative_path(context), self._read_limited(target))

    def _target(self, context: StageContext) -> Path:
        try:
            return self._confinement.resolve(video_clip_manifest_relative_path(context))
        except Exception as exc:
            raise VideoClipManifestCorruptException("video clip manifest path is unsafe") from exc

    def _read(self, target: Path) -> ProductionVideoClipManifest:
        try:
            return deserialize_video_clip_manifest(self._read_limited(target))
        except VideoClipManifestCorruptException:
            raise
        except (UnicodeError, ValueError, TypeError) as exc:
            raise VideoClipManifestCorruptException("video clip manifest is invalid") from exc

    def _read_limited(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise VideoClipManifestCorruptException(
                    "video clip manifest exceeds configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except VideoClipManifestCorruptException:
            raise
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise VideoClipManifestCorruptException(
                "video clip manifest could not be read safely"
            ) from exc
        if len(content) > self._maximum:
            raise VideoClipManifestCorruptException("video clip manifest exceeds configured limit")
        return content

    def _replace(self, target: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise VideoClipManifestConflictException("video clip manifest exceeds configured limit")
        descriptor = -1
        temporary: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise VideoClipManifestConflictException(
                "video clip manifest could not be replaced safely"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        try:
            descriptor = os.open(
                lock,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise VideoClipManifestConflictException(
                "video clip manifest is locked by another writer"
            ) from exc
        except OSError as exc:
            raise VideoClipManifestConflictException(
                "video clip manifest lock could not be created"
            ) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise VideoClipManifestConflictException(
                    "video clip manifest lock could not be released"
                ) from exc


def video_clip_manifest_relative_path(context: StageContext) -> str:
    expected = (
        f"production/{context.job_id}/generating_video_clips/"
        f"attempt-{context.attempt_number}/video-clip-generation-manifest.json"
    )
    normalized = validate_relative_path(
        f"{context.workspace_relative_path}/video-clip-generation-manifest.json"
    )
    if normalized != expected or "\\" in normalized:
        raise VideoClipManifestConflictException("video clip manifest path is not contractual")
    return normalized


def _validate_transition(
    previous: ProductionVideoClipManifest,
    current: ProductionVideoClipManifest,
) -> None:
    try:
        validate_manifest_transition(previous, current)
    except ValueError as exc:
        raise VideoClipManifestConflictException(str(exc)) from exc


def _written(path: str, content: bytes) -> WrittenVideoClipManifest:
    try:
        manifest = deserialize_video_clip_manifest(content)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise VideoClipManifestCorruptException("video clip manifest is invalid") from exc
    return WrittenVideoClipManifest(
        relative_path=path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        manifest=manifest,
    )


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
