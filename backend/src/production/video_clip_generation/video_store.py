"""Specialized write-once durable MP4 clip store."""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.video_clip_generation.exceptions import (
    VideoClipConflictError,
    VideoClipIntegrityError,
    VideoClipLinkError,
    VideoClipNotFoundError,
    VideoClipPathError,
    VideoClipStoreError,
)
from backend.src.production.video_clip_generation.media_probe import (
    VideoClipIntegrityValidator,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipAsset,
    ReadProductionVideoClipAsset,
    VideoClipWriteRequest,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_asset,
    serialize_video_clip_asset,
)


class FilesystemVideoClipBinaryStore:
    """Store only verified MP4 clips under the contractual video-clips path."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        integrity_validator: VideoClipIntegrityValidator,
        max_video_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._integrity = integrity_validator
        self._maximum = max_video_bytes
        self._clock = clock

    async def write(
        self, *, request: VideoClipWriteRequest, content: bytes
    ) -> ProductionVideoClipAsset:
        inspected = await self._integrity.validate_content(
            content,
            expected_width=request.expected_width,
            expected_height=request.expected_height,
            expected_duration_seconds=request.expected_duration_seconds,
            expected_frame_rate=request.expected_frame_rate,
        )
        digest = hashlib.sha256(content).hexdigest()
        asset = ProductionVideoClipAsset(
            asset_id=request.asset_id,
            job_id=request.job_id,
            scene_id=request.scene_id,
            shot_id=request.shot_id,
            role=request.role,
            sha256=digest,
            size_bytes=len(content),
            width=inspected.width,
            height=inspected.height,
            duration_seconds=inspected.duration_seconds,
            frame_rate=inspected.frame_rate,
            frame_count=inspected.frame_count,
            video_codec=inspected.video_codec,
            audio_codec=None,
            has_audio=False,
            created_at=self._aware_now(),
            storage_path=video_clip_relative_path(
                job_id=request.job_id, visual_asset_id=request.visual_asset_id
            ),
            metadata=request.metadata,
        )
        result = await asyncio.to_thread(self._write_sync, asset, content)
        await self._validate_asset_file(result)
        return result

    async def resolve(self, *, job_id: UUID, visual_asset_id: str) -> ReadProductionVideoClipAsset:
        relative_path = video_clip_relative_path(job_id=job_id, visual_asset_id=visual_asset_id)
        asset = await asyncio.to_thread(self._read_sidecar_sync, relative_path)
        if asset.job_id != job_id or asset.asset_id != f"video-{visual_asset_id}":
            raise VideoClipIntegrityError("video clip identity differs from sidecar")
        return await self.read(asset=asset)

    async def read(self, *, asset: ProductionVideoClipAsset) -> ReadProductionVideoClipAsset:
        target, content = await asyncio.to_thread(self._read_content_sync, asset)
        await self._validate_asset_file(asset, target=target)
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise VideoClipIntegrityError("video clip checksum differs from sidecar")
        if len(content) != asset.size_bytes:
            raise VideoClipIntegrityError("video clip size differs from sidecar")
        return ReadProductionVideoClipAsset(asset=asset, content=content)

    def _write_sync(
        self, asset: ProductionVideoClipAsset, content: bytes
    ) -> ProductionVideoClipAsset:
        target = self._resolve(asset.storage_path)
        sidecar = self._resolve(f"{asset.storage_path}.asset.json")
        self._create_parent(target.parent)
        with self._exclusive_lock(target):
            if target.exists() or sidecar.exists():
                if not target.exists() or not sidecar.exists():
                    raise VideoClipConflictError("video clip has incomplete durable metadata")
                existing = self._read_sidecar_file(sidecar)
                if existing != asset:
                    # created_at and provider latency are descriptive; integrity and
                    # provenance remain strict for safe idempotent reuse.
                    comparable = existing.model_copy(update={"created_at": asset.created_at})
                    if comparable != asset:
                        raise VideoClipConflictError("existing video clip is incompatible")
                _, existing_content = self._read_content_sync(existing)
                if hashlib.sha256(existing_content).hexdigest() != existing.sha256:
                    raise VideoClipConflictError("existing video clip failed checksum validation")
                return existing
            self._atomic_write(target, content)
            self._atomic_write(sidecar, serialize_video_clip_asset(asset))
            return asset

    async def _validate_asset_file(
        self, asset: ProductionVideoClipAsset, *, target: Path | None = None
    ) -> None:
        resolved = target or self._resolve(asset.storage_path, require_exists=True)
        inspected = await self._integrity.validate_path(
            resolved,
            expected_width=asset.width,
            expected_height=asset.height,
            expected_duration_seconds=asset.duration_seconds,
            expected_frame_rate=asset.frame_rate,
        )
        if (
            inspected.frame_count != asset.frame_count
            or inspected.video_codec != asset.video_codec
            or inspected.has_audio != asset.has_audio
        ):
            raise VideoClipIntegrityError("video clip probe metadata differs from sidecar")

    def _read_sidecar_sync(self, relative_path: str) -> ProductionVideoClipAsset:
        sidecar = self._resolve(f"{relative_path}.asset.json", require_exists=True)
        return self._read_sidecar_file(sidecar)

    def _read_sidecar_file(self, path: Path) -> ProductionVideoClipAsset:
        try:
            self._confinement.reject_unsafe_file(path)
            with path.open("rb") as stream:
                content = stream.read(128_001)
            if len(content) > 128_000:
                raise VideoClipIntegrityError("video clip sidecar exceeds safe limit")
            return deserialize_video_clip_asset(content)
        except VideoClipStoreError:
            raise
        except BinaryAssetLinkError as exc:
            raise VideoClipLinkError("video clip sidecar contains a link") from exc
        except BinaryAssetPathError as exc:
            raise VideoClipPathError("video clip sidecar path is unsafe") from exc
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise VideoClipIntegrityError("video clip sidecar is invalid") from exc

    def _read_content_sync(self, asset: ProductionVideoClipAsset) -> tuple[Path, bytes]:
        target = self._resolve(asset.storage_path, require_exists=True)
        try:
            self._confinement.reject_unsafe_file(target)
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
                status = os.fstat(stream.fileno())
        except BinaryAssetLinkError as exc:
            raise VideoClipLinkError("video clip contains a link") from exc
        except BinaryAssetPathError as exc:
            raise VideoClipPathError("video clip path is unsafe") from exc
        except OSError as exc:
            raise VideoClipNotFoundError("video clip could not be read") from exc
        if status.st_nlink != 1:
            raise VideoClipLinkError("video clip hard links are not allowed")
        if len(content) > self._maximum:
            raise VideoClipIntegrityError("video clip exceeds configured limit")
        return target, content

    def _resolve(self, relative_path: str, *, require_exists: bool = False) -> Path:
        try:
            return self._confinement.resolve(relative_path, require_exists=require_exists)
        except BinaryAssetLinkError as exc:
            raise VideoClipLinkError("video clip path contains a link") from exc
        except BinaryAssetPathError as exc:
            raise VideoClipPathError("video clip path is unsafe") from exc
        except Exception as exc:
            if require_exists:
                raise VideoClipNotFoundError("video clip file is missing") from exc
            raise

    def _create_parent(self, parent: Path) -> None:
        try:
            parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(parent)
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise VideoClipPathError("video clip directory could not be created safely") from exc

    @contextmanager
    def _exclusive_lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise VideoClipConflictError("video clip is already being written") from exc
        except OSError as exc:
            raise VideoClipStoreError("video clip lock could not be created") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise VideoClipStoreError("video clip lock could not be released") from exc

    def _atomic_write(self, target: Path, content: bytes) -> None:
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists() or target.is_symlink():
                raise VideoClipConflictError("video clip target appeared concurrently")
            os.replace(temporary, target)
            temporary = None
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        except VideoClipConflictError:
            raise
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise VideoClipStoreError("video clip could not be written") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("video clip clock must be timezone-aware")
        return value


def video_clip_relative_path(*, job_id: UUID, visual_asset_id: str) -> str:
    return f"production/{job_id}/assets/video-clips/video-{visual_asset_id}.mp4"


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
