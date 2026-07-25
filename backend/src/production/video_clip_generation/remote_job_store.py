"""Durable write-once/CAS storage for asynchronous remote video jobs."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.video_clip_generation.exceptions import (
    RemoteVideoJobConflictError,
    RemoteVideoJobStoreError,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterRemoteStatus,
    RemoteVideoJobRecord,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_remote_video_job,
    serialize_remote_video_job,
)

_VISUAL_ASSET_ID = re.compile(r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")


class InMemoryRemoteVideoJobStore:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, int, str], RemoteVideoJobRecord] = {}
        self.checkpoints = 0

    async def read(
        self, *, job_id: UUID, attempt_number: int, visual_asset_id: str
    ) -> RemoteVideoJobRecord | None:
        return self.records.get((job_id, attempt_number, visual_asset_id))

    async def create(self, record: RemoteVideoJobRecord) -> None:
        key = (UUID(record.job_id), record.attempt_number, record.visual_asset_id)
        if key in self.records:
            raise RemoteVideoJobConflictError("remote video job already exists")
        self.records[key] = record
        self.checkpoints += 1

    async def find_latest(
        self,
        *,
        job_id: UUID,
        before_attempt_number: int,
        visual_asset_id: str,
    ) -> RemoteVideoJobRecord | None:
        attempts = [
            attempt
            for (candidate_job, attempt, candidate_asset), record in self.records.items()
            if candidate_job == job_id
            and attempt < before_attempt_number
            and candidate_asset == visual_asset_id
            and _is_reusable(record)
        ]
        if not attempts:
            return None
        return self.records[(job_id, max(attempts), visual_asset_id)]

    async def checkpoint(
        self, *, previous: RemoteVideoJobRecord, current: RemoteVideoJobRecord
    ) -> None:
        _validate_transition(previous, current)
        key = (
            UUID(previous.job_id),
            previous.attempt_number,
            previous.visual_asset_id,
        )
        if self.records.get(key) != previous:
            raise RemoteVideoJobConflictError("remote video job changed concurrently")
        self.records[key] = current
        self.checkpoints += 1


class LocalRemoteVideoJobStore:
    def __init__(self, workspace_root: Path, *, max_bytes: int = 1_000_000) -> None:
        if not 1 <= max_bytes <= 4_000_000:
            raise ValueError("remote job maximum size is outside safe limits")
        self._root = workspace_root
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_bytes

    async def read(
        self, *, job_id: UUID, attempt_number: int, visual_asset_id: str
    ) -> RemoteVideoJobRecord | None:
        return await asyncio.to_thread(self._read_sync, job_id, attempt_number, visual_asset_id)

    async def create(self, record: RemoteVideoJobRecord) -> None:
        await asyncio.to_thread(self._create_sync, record)

    async def find_latest(
        self,
        *,
        job_id: UUID,
        before_attempt_number: int,
        visual_asset_id: str,
    ) -> RemoteVideoJobRecord | None:
        if before_attempt_number < 2:
            return None
        for attempt in range(before_attempt_number - 1, 0, -1):
            record = await self.read(
                job_id=job_id,
                attempt_number=attempt,
                visual_asset_id=visual_asset_id,
            )
            if record is not None and _is_reusable(record):
                return record
        return None

    async def checkpoint(
        self, *, previous: RemoteVideoJobRecord, current: RemoteVideoJobRecord
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, previous, current)

    def _read_sync(
        self, job_id: UUID, attempt_number: int, visual_asset_id: str
    ) -> RemoteVideoJobRecord | None:
        path = self._path(job_id, attempt_number, visual_asset_id)
        if not path.exists():
            return None
        return self._read_file(path)

    def _create_sync(self, record: RemoteVideoJobRecord) -> None:
        path = self._path(UUID(record.job_id), record.attempt_number, record.visual_asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(path)
        with self._exclusive_lock(path):
            if path.exists() or path.is_symlink():
                raise RemoteVideoJobConflictError(
                    "remote video job already exists"
                )
            self._replace(path, serialize_remote_video_job(record))

    def _checkpoint_sync(
        self, previous: RemoteVideoJobRecord, current: RemoteVideoJobRecord
    ) -> None:
        _validate_transition(previous, current)
        path = self._path(UUID(previous.job_id), previous.attempt_number, previous.visual_asset_id)
        with self._exclusive_lock(path):
            if self._read_file(path) != previous:
                raise RemoteVideoJobConflictError(
                    "remote video job changed concurrently"
                )
            self._replace(path, serialize_remote_video_job(current))

    def _read_file(self, path: Path) -> RemoteVideoJobRecord:
        try:
            self._confinement.reject_unsafe_file(path)
            if path.stat().st_nlink != 1:
                raise RemoteVideoJobStoreError("remote job hard links are not allowed")
            content = path.read_bytes()
            if not content or len(content) > self._maximum:
                raise RemoteVideoJobStoreError("remote job file size is invalid")
            return deserialize_remote_video_job(content)
        except RemoteVideoJobStoreError:
            raise
        except (OSError, ValueError) as exc:
            raise RemoteVideoJobStoreError("remote video job is invalid") from exc

    def _replace(self, path: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise RemoteVideoJobStoreError("remote job exceeds safe limit")
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temp = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise RemoteVideoJobStoreError("remote video job could not be persisted") from exc
        finally:
            temp.unlink(missing_ok=True)

    def _path(self, job_id: UUID, attempt: int, visual_asset_id: str) -> Path:
        if attempt < 1 or _VISUAL_ASSET_ID.fullmatch(visual_asset_id) is None:
            raise RemoteVideoJobStoreError("remote video job identity is invalid")
        relative = PurePosixPath(
            "production",
            str(job_id),
            "generating_video_clips",
            f"attempt-{attempt}",
            "remote-jobs",
            f"video-{visual_asset_id}.json",
        )
        return self._root.joinpath(*relative.parts)

    @contextmanager
    def _exclusive_lock(self, path: Path) -> Iterator[None]:
        lock = path.with_name(f".{path.name}.lock")
        descriptor = -1
        acquired = False
        try:
            descriptor = os.open(
                lock,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            descriptor = -1
            acquired = True
            yield
        except FileExistsError as exc:
            raise RemoteVideoJobConflictError(
                "remote video job is locked by another worker"
            ) from exc
        except OSError as exc:
            raise RemoteVideoJobStoreError(
                "remote video job lock could not be created"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if acquired:
                try:
                    lock.unlink(missing_ok=True)
                except OSError as exc:
                    raise RemoteVideoJobStoreError(
                        "remote video job lock could not be released"
                    ) from exc


def _validate_transition(previous: RemoteVideoJobRecord, current: RemoteVideoJobRecord) -> None:
    immutable = (
        "job_id",
        "attempt_number",
        "visual_asset_id",
        "provider",
        "model",
        "source_image_sha256",
        "prompt_sha256",
        "capability_snapshot_hash",
        "provider_request_fingerprint",
        "publication_provider",
        "publication_id",
        "remote_job_id",
        "estimated_cost_usd",
        "pricing_snapshot_at",
        "pricing_sku",
        "safe_remote_path",
    )
    if any(getattr(previous, item) != getattr(current, item) for item in immutable):
        raise RemoteVideoJobConflictError("remote video job immutable fields changed")
    if current.poll_attempts < previous.poll_attempts:
        raise RemoteVideoJobConflictError("remote poll attempts cannot decrease")


def _is_reusable(record: RemoteVideoJobRecord) -> bool:
    return record.remote_status not in {
        OpenRouterRemoteStatus.FAILED,
        OpenRouterRemoteStatus.CANCELLED,
        OpenRouterRemoteStatus.EXPIRED,
    }
