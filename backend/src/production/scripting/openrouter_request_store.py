"""Atomic confined storage for controlled OpenRouter scripting checkpoints."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Protocol
from uuid import UUID

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingRequestRecord,
    validate_openrouter_scripting_request_transition,
)
from backend.src.production.scripting.openrouter_serialization import (
    deserialize_openrouter_scripting_request,
    serialize_openrouter_scripting_request,
)


class OpenRouterScriptingRequestStoreError(RuntimeError):
    pass


class OpenRouterScriptingRequestConflictError(OpenRouterScriptingRequestStoreError):
    pass


class OpenRouterScriptingRequestCorruptError(OpenRouterScriptingRequestStoreError):
    pass


class OpenRouterScriptingRequestStore(Protocol):
    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
    ) -> OpenRouterScriptingRequestRecord | None: ...

    async def create(self, record: OpenRouterScriptingRequestRecord) -> None: ...

    async def checkpoint(
        self,
        *,
        previous: OpenRouterScriptingRequestRecord,
        current: OpenRouterScriptingRequestRecord,
    ) -> None: ...

    async def list_for_job(
        self,
        *,
        job_id: UUID,
    ) -> tuple[OpenRouterScriptingRequestRecord, ...]: ...


class InMemoryOpenRouterScriptingRequestStore:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, int], OpenRouterScriptingRequestRecord] = {}
        self.checkpoints = 0

    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
    ) -> OpenRouterScriptingRequestRecord | None:
        return self.records.get((job_id, attempt_number))

    async def create(self, record: OpenRouterScriptingRequestRecord) -> None:
        key = (record.job_id, record.attempt_number)
        if key in self.records:
            raise OpenRouterScriptingRequestConflictError(
                "OpenRouter scripting request already exists"
            )
        self.records[key] = _validated(record)
        self.checkpoints += 1

    async def checkpoint(
        self,
        *,
        previous: OpenRouterScriptingRequestRecord,
        current: OpenRouterScriptingRequestRecord,
    ) -> None:
        _validate_transition(previous, current)
        key = (previous.job_id, previous.attempt_number)
        if self.records.get(key) != previous:
            raise OpenRouterScriptingRequestConflictError(
                "OpenRouter scripting request changed concurrently"
            )
        self.records[key] = _validated(current)
        self.checkpoints += 1

    async def list_for_job(
        self,
        *,
        job_id: UUID,
    ) -> tuple[OpenRouterScriptingRequestRecord, ...]:
        return tuple(
            self.records[key]
            for key in sorted(self.records, key=lambda item: (str(item[0]), item[1]))
            if key[0] == job_id
        )


class LocalOpenRouterScriptingRequestStore:
    def __init__(self, workspace_root: Path, *, max_bytes: int = 2_000_000) -> None:
        if not 1_024 <= max_bytes <= 4_000_000:
            raise ValueError("OpenRouter scripting request limit is outside safe bounds")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_bytes

    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
    ) -> OpenRouterScriptingRequestRecord | None:
        return await asyncio.to_thread(self._read_sync, job_id, attempt_number)

    async def create(self, record: OpenRouterScriptingRequestRecord) -> None:
        await asyncio.to_thread(self._create_sync, record)

    async def checkpoint(
        self,
        *,
        previous: OpenRouterScriptingRequestRecord,
        current: OpenRouterScriptingRequestRecord,
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, previous, current)

    async def list_for_job(
        self,
        *,
        job_id: UUID,
    ) -> tuple[OpenRouterScriptingRequestRecord, ...]:
        return await asyncio.to_thread(self._list_for_job_sync, job_id)

    def _read_sync(
        self,
        job_id: UUID,
        attempt_number: int,
    ) -> OpenRouterScriptingRequestRecord | None:
        path = self._path(job_id, attempt_number)
        if not path.exists() and not path.is_symlink():
            return None
        return self._read_file(path)

    def _create_sync(self, record: OpenRouterScriptingRequestRecord) -> None:
        validated = _validated(record)
        path = self._path(validated.job_id, validated.attempt_number)
        self._create_parent(path.parent)
        with self._exclusive_lock(path):
            if path.exists() or path.is_symlink():
                raise OpenRouterScriptingRequestConflictError(
                    "OpenRouter scripting request already exists"
                )
            self._replace(path, serialize_openrouter_scripting_request(validated))

    def _checkpoint_sync(
        self,
        previous: OpenRouterScriptingRequestRecord,
        current: OpenRouterScriptingRequestRecord,
    ) -> None:
        _validate_transition(previous, current)
        path = self._path(previous.job_id, previous.attempt_number)
        with self._exclusive_lock(path):
            if self._read_file(path) != previous:
                raise OpenRouterScriptingRequestConflictError(
                    "OpenRouter scripting request changed concurrently"
                )
            self._replace(path, serialize_openrouter_scripting_request(current))

    def _list_for_job_sync(
        self,
        job_id: UUID,
    ) -> tuple[OpenRouterScriptingRequestRecord, ...]:
        pattern = f"production/{job_id}/scripting/attempt-*/openrouter-scripting-request.json"
        return tuple(self._read_file(path) for path in sorted(self._confinement.root.glob(pattern)))

    def _read_file(self, path: Path) -> OpenRouterScriptingRequestRecord:
        try:
            self._confinement.reject_unsafe_components(path)
            self._confinement.reject_unsafe_file(path)
            with path.open("rb") as stream:
                content = stream.read(self._maximum + 1)
            if not content or len(content) > self._maximum:
                raise OpenRouterScriptingRequestCorruptError(
                    "OpenRouter scripting request file size is invalid"
                )
            record = deserialize_openrouter_scripting_request(content)
            if self._path(record.job_id, record.attempt_number) != path:
                raise OpenRouterScriptingRequestCorruptError(
                    "OpenRouter scripting request path identity differs"
                )
            return record
        except OpenRouterScriptingRequestStoreError:
            raise
        except (BinaryAssetError, OSError, UnicodeError, ValueError) as exc:
            raise OpenRouterScriptingRequestCorruptError(
                "OpenRouter scripting request is invalid"
            ) from exc

    def _path(self, job_id: UUID, attempt_number: int) -> Path:
        if attempt_number < 1:
            raise OpenRouterScriptingRequestStoreError("OpenRouter scripting attempt is invalid")
        relative = (
            f"production/{job_id}/scripting/attempt-{attempt_number}/"
            "openrouter-scripting-request.json"
        )
        try:
            return self._confinement.resolve(relative)
        except BinaryAssetError as exc:
            raise OpenRouterScriptingRequestStoreError(
                "OpenRouter scripting request path is unsafe"
            ) from exc

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
            raise OpenRouterScriptingRequestStoreError(
                "OpenRouter scripting request directory is unsafe"
            ) from exc

    def _replace(self, path: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise OpenRouterScriptingRequestStoreError(
                "OpenRouter scripting request exceeds safe limit"
            )
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                dir=path.parent,
                prefix=".openrouter-scripting-",
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
            raise OpenRouterScriptingRequestStoreError(
                "OpenRouter scripting request could not be persisted"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_lock(self, path: Path) -> Iterator[None]:
        lock = path.with_name(".openrouter-scripting.lock")
        descriptor = -1
        acquired = False
        try:
            self._confinement.reject_unsafe_components(lock)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            descriptor = -1
            acquired = True
            yield
        except FileExistsError as exc:
            raise OpenRouterScriptingRequestConflictError(
                "OpenRouter scripting request is locked"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if acquired:
                lock.unlink(missing_ok=True)


def _validate_transition(
    previous: OpenRouterScriptingRequestRecord,
    current: OpenRouterScriptingRequestRecord,
) -> None:
    try:
        validate_openrouter_scripting_request_transition(previous, current)
    except ValueError as exc:
        raise OpenRouterScriptingRequestConflictError(
            "OpenRouter scripting request transition is invalid"
        ) from exc


def _validated(
    record: OpenRouterScriptingRequestRecord,
) -> OpenRouterScriptingRequestRecord:
    return OpenRouterScriptingRequestRecord.model_validate(record.model_dump(mode="python"))


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


__all__ = [
    "InMemoryOpenRouterScriptingRequestStore",
    "LocalOpenRouterScriptingRequestStore",
    "OpenRouterScriptingRequestConflictError",
    "OpenRouterScriptingRequestCorruptError",
    "OpenRouterScriptingRequestStore",
]
