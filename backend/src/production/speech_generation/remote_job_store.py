"""Durable write-once/CAS storage for future remote speech jobs."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import UUID

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.speech_generation.exceptions import (
    RemoteSpeechJobConflictError,
    RemoteSpeechJobCorruptError,
    RemoteSpeechJobStoreError,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    validate_remote_speech_job_transition,
)
from backend.src.production.speech_generation.remote_serialization import (
    deserialize_remote_speech_job,
    serialize_remote_speech_job,
)


class InMemoryRemoteSpeechJobStore:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, int, str], RemoteSpeechJobRecord] = {}
        self.checkpoints = 0

    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        segment_id: str,
    ) -> RemoteSpeechJobRecord | None:
        return self.records.get((job_id, attempt_number, segment_id))

    async def create(self, record: RemoteSpeechJobRecord) -> None:
        key = (record.job_id, record.attempt_number, record.segment_id)
        if key in self.records:
            raise RemoteSpeechJobConflictError("remote speech job already exists")
        self.records[key] = _validated(record)
        self.checkpoints += 1

    async def checkpoint(
        self,
        *,
        previous: RemoteSpeechJobRecord,
        current: RemoteSpeechJobRecord,
    ) -> None:
        try:
            validate_remote_speech_job_transition(previous, current)
        except ValueError as exc:
            raise RemoteSpeechJobConflictError("remote speech job transition is invalid") from exc
        key = (previous.job_id, previous.attempt_number, previous.segment_id)
        if self.records.get(key) != previous:
            raise RemoteSpeechJobConflictError("remote speech job changed concurrently")
        self.records[key] = current
        self.checkpoints += 1

    async def list_records(self) -> tuple[RemoteSpeechJobRecord, ...]:
        return tuple(
            self.records[key]
            for key in sorted(
                self.records,
                key=lambda item: (str(item[0]), item[1], item[2]),
            )
        )


class LocalRemoteSpeechJobStore:
    def __init__(self, workspace_root: Path, *, max_bytes: int = 1_000_000) -> None:
        if not 1_024 <= max_bytes <= 4_000_000:
            raise ValueError("remote speech job maximum size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_bytes

    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        segment_id: str,
    ) -> RemoteSpeechJobRecord | None:
        return await asyncio.to_thread(
            self._read_sync,
            job_id,
            attempt_number,
            segment_id,
        )

    async def create(self, record: RemoteSpeechJobRecord) -> None:
        await asyncio.to_thread(self._create_sync, record)

    async def checkpoint(
        self,
        *,
        previous: RemoteSpeechJobRecord,
        current: RemoteSpeechJobRecord,
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, previous, current)

    async def list_records(self) -> tuple[RemoteSpeechJobRecord, ...]:
        return await asyncio.to_thread(self._list_records_sync)

    def _read_sync(
        self,
        job_id: UUID,
        attempt_number: int,
        segment_id: str,
    ) -> RemoteSpeechJobRecord | None:
        path = self._path(job_id, attempt_number, segment_id)
        if not path.exists() and not path.is_symlink():
            return None
        return self._read_file(path)

    def _create_sync(self, record: RemoteSpeechJobRecord) -> None:
        validated = _validated(record)
        path = self._path(
            validated.job_id,
            validated.attempt_number,
            validated.segment_id,
        )
        self._create_parent(path.parent)
        with self._exclusive_lock(path):
            if path.exists() or path.is_symlink():
                raise RemoteSpeechJobConflictError("remote speech job already exists")
            self._replace(path, serialize_remote_speech_job(validated))

    def _checkpoint_sync(
        self,
        previous: RemoteSpeechJobRecord,
        current: RemoteSpeechJobRecord,
    ) -> None:
        try:
            validate_remote_speech_job_transition(previous, current)
        except ValueError as exc:
            raise RemoteSpeechJobConflictError("remote speech job transition is invalid") from exc
        path = self._path(
            previous.job_id,
            previous.attempt_number,
            previous.segment_id,
        )
        with self._exclusive_lock(path):
            if self._read_file(path) != previous:
                raise RemoteSpeechJobConflictError("remote speech job changed concurrently")
            self._replace(path, serialize_remote_speech_job(current))

    def _list_records_sync(self) -> tuple[RemoteSpeechJobRecord, ...]:
        pattern = "production/*/generating_narration/attempt-*/remote-speech-jobs/segment-*.json"
        records: list[RemoteSpeechJobRecord] = []
        for path in sorted(self._confinement.root.glob(pattern)):
            records.append(self._read_file(path))
        return tuple(records)

    def _read_file(self, path: Path) -> RemoteSpeechJobRecord:
        try:
            self._confinement.reject_unsafe_components(path)
            self._confinement.reject_unsafe_file(path)
            with path.open("rb") as stream:
                content = stream.read(self._maximum + 1)
            if not content or len(content) > self._maximum:
                raise RemoteSpeechJobCorruptError("remote speech job file size is invalid")
            record = deserialize_remote_speech_job(content)
            expected = self._path(
                record.job_id,
                record.attempt_number,
                record.segment_id,
            )
            if expected != path:
                raise RemoteSpeechJobCorruptError("remote speech job path identity differs")
            return record
        except RemoteSpeechJobStoreError:
            raise
        except (BinaryAssetError, OSError, UnicodeError, ValueError) as exc:
            raise RemoteSpeechJobCorruptError("remote speech job is invalid") from exc

    def _path(self, job_id: UUID, attempt: int, segment_id: str) -> Path:
        if (
            attempt < 1
            or not segment_id.startswith("segment-")
            or len(segment_id) != 40
            or any(character not in "0123456789abcdef" for character in segment_id[8:])
        ):
            raise RemoteSpeechJobStoreError("remote speech job identity is invalid")
        relative = (
            f"production/{job_id}/generating_narration/attempt-{attempt}/"
            f"remote-speech-jobs/{segment_id}.json"
        )
        try:
            return self._confinement.resolve(relative)
        except BinaryAssetError as exc:
            raise RemoteSpeechJobStoreError("remote speech job path is unsafe") from exc

    def _create_parent(self, parent: Path) -> None:
        try:
            relative = parent.relative_to(self._confinement.root)
            current = self._confinement.root
            for part in relative.parts:
                current /= part
                if not current.exists():
                    with suppress(FileExistsError):
                        current.mkdir()
                self._confinement.reject_unsafe_components(current)
        except (BinaryAssetError, OSError, ValueError) as exc:
            raise RemoteSpeechJobStoreError("remote speech job directory is unsafe") from exc

    def _replace(self, path: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise RemoteSpeechJobStoreError("remote speech job exceeds safe limit")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                dir=path.parent,
                prefix=".remote-speech-",
                suffix=".tmp",
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            _fsync_directory(path.parent)
        except OSError as exc:
            raise RemoteSpeechJobStoreError("remote speech job could not be persisted") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_lock(self, path: Path) -> Iterator[None]:
        lock = path.with_name(f".rs-{path.stem[-12:]}.lock")
        descriptor = -1
        acquired = False
        try:
            self._confinement.reject_unsafe_components(lock)
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
            raise RemoteSpeechJobConflictError(
                "remote speech job is locked by another worker"
            ) from exc
        except RemoteSpeechJobStoreError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise RemoteSpeechJobStoreError("remote speech job lock could not be created") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if acquired:
                try:
                    lock.unlink(missing_ok=True)
                except OSError as exc:
                    raise RemoteSpeechJobStoreError(
                        "remote speech job lock could not be released"
                    ) from exc


def remote_speech_job_relative_path(record: RemoteSpeechJobRecord) -> str:
    return (
        f"production/{record.job_id}/generating_narration/"
        f"attempt-{record.attempt_number}/remote-speech-jobs/"
        f"{record.segment_id}.json"
    )


def _validated(record: RemoteSpeechJobRecord) -> RemoteSpeechJobRecord:
    return RemoteSpeechJobRecord.model_validate(record.model_dump(mode="python"))


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
