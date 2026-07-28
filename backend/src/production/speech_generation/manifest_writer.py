"""Atomic compare-and-swap speech manifest checkpoints."""

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
from backend.src.production.speech_generation.exceptions import (
    SpeechManifestConflictError,
    SpeechManifestCorruptError,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifest,
    SpeechGenerationManifestStatus,
    validate_speech_manifest_transition,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
    serialize_speech_manifest,
)


class WrittenSpeechManifest(ContractModel):
    relative_path: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest: SpeechGenerationManifest


class InMemorySpeechManifestWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}
        self.checkpoint_count = 0

    async def read_existing(self, *, context: StageContext) -> SpeechGenerationManifest | None:
        content = self.contents.get(speech_manifest_relative_path(context))
        return deserialize_speech_manifest(content) if content is not None else None

    async def create(self, *, context: StageContext, manifest: SpeechGenerationManifest) -> None:
        manifest = _validated(manifest)
        path = speech_manifest_relative_path(context)
        if path in self.contents:
            raise SpeechManifestConflictError("speech manifest already exists")
        self.contents[path] = serialize_speech_manifest(manifest)
        self.checkpoint_count += 1

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None:
        _validate_transition(previous, current)
        path = speech_manifest_relative_path(context)
        if self.contents.get(path) != serialize_speech_manifest(previous):
            raise SpeechManifestConflictError("speech checkpoint changed concurrently")
        self.contents[path] = serialize_speech_manifest(current)
        self.checkpoint_count += 1

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None:
        if current.status is not SpeechGenerationManifestStatus.COMPLETED:
            raise SpeechManifestConflictError("final speech manifest must be completed")
        await self.checkpoint(context=context, previous=previous, current=current)

    def written(self, *, context: StageContext) -> WrittenSpeechManifest:
        path = speech_manifest_relative_path(context)
        return _written(path, self.contents[path])


class LocalSpeechManifestWriter:
    def __init__(self, workspace_root: Path, *, max_manifest_bytes: int) -> None:
        if not 1_024 <= max_manifest_bytes <= 16_000_000:
            raise ValueError("maximum speech manifest size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_manifest_bytes

    async def read_existing(self, *, context: StageContext) -> SpeechGenerationManifest | None:
        return await asyncio.to_thread(self._read_existing_sync, context)

    async def create(self, *, context: StageContext, manifest: SpeechGenerationManifest) -> None:
        await asyncio.to_thread(self._create_sync, context, manifest)

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, context, previous, current)

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None:
        if current.status is not SpeechGenerationManifestStatus.COMPLETED:
            raise SpeechManifestConflictError("final speech manifest must be completed")
        await self.checkpoint(context=context, previous=previous, current=current)

    async def written(self, *, context: StageContext) -> WrittenSpeechManifest:
        return await asyncio.to_thread(self._written_sync, context)

    def _read_existing_sync(self, context: StageContext) -> SpeechGenerationManifest | None:
        target = self._target(context)
        if not target.exists():
            return None
        return self._read(target)

    def _create_sync(
        self,
        context: StageContext,
        manifest: SpeechGenerationManifest,
    ) -> None:
        manifest = _validated(manifest)
        target = self._target(context)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target)
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise SpeechManifestConflictError("speech manifest directory is unsafe") from exc
        with self._exclusive_lock(target):
            if target.exists() or target.is_symlink():
                raise SpeechManifestConflictError("speech manifest already exists")
            self._replace(target, serialize_speech_manifest(manifest))

    def _checkpoint_sync(
        self,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None:
        _validate_transition(previous, current)
        target = self._target(context)
        with self._exclusive_lock(target):
            if self._read(target) != previous:
                raise SpeechManifestConflictError("speech checkpoint changed concurrently")
            self._replace(target, serialize_speech_manifest(current))

    def _written_sync(self, context: StageContext) -> WrittenSpeechManifest:
        target = self._target(context)
        return _written(speech_manifest_relative_path(context), self._read_limited(target))

    def _target(self, context: StageContext) -> Path:
        try:
            return self._confinement.resolve(speech_manifest_relative_path(context))
        except Exception as exc:
            raise SpeechManifestCorruptError("speech manifest path is unsafe") from exc

    def _read(self, target: Path) -> SpeechGenerationManifest:
        try:
            return deserialize_speech_manifest(self._read_limited(target))
        except SpeechManifestCorruptError:
            raise
        except (UnicodeError, ValueError, TypeError) as exc:
            raise SpeechManifestCorruptError("speech manifest is invalid") from exc

    def _read_limited(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise SpeechManifestCorruptError("speech manifest exceeds configured limit")
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except SpeechManifestCorruptError:
            raise
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise SpeechManifestCorruptError("speech manifest could not be read safely") from exc
        if len(content) > self._maximum:
            raise SpeechManifestCorruptError("speech manifest exceeds configured limit")
        return content

    def _replace(self, target: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise SpeechManifestConflictError("speech manifest exceeds configured limit")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".speech-manifest-",
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
            raise SpeechManifestConflictError("speech manifest could not be replaced") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise SpeechManifestConflictError("speech manifest is locked") from exc
        except OSError as exc:
            raise SpeechManifestConflictError("speech manifest lock failed") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise SpeechManifestConflictError("speech manifest lock release failed") from exc


def speech_manifest_relative_path(context: StageContext) -> str:
    expected = (
        f"production/{context.job_id}/generating_narration/"
        f"attempt-{context.attempt_number}/speech-generation-manifest.json"
    )
    normalized = validate_relative_path(
        f"{context.workspace_relative_path}/speech-generation-manifest.json"
    )
    if normalized != expected or "\\" in normalized:
        raise SpeechManifestConflictError("speech manifest path is not contractual")
    return normalized


def _validate_transition(
    previous: SpeechGenerationManifest,
    current: SpeechGenerationManifest,
) -> None:
    try:
        previous = _validated(previous)
        current = _validated(current)
        validate_speech_manifest_transition(previous, current)
    except ValueError as exc:
        raise SpeechManifestConflictError(str(exc)) from exc


def _validated(manifest: SpeechGenerationManifest) -> SpeechGenerationManifest:
    try:
        return SpeechGenerationManifest.model_validate(manifest.model_dump(mode="python"))
    except (TypeError, ValueError) as exc:
        raise SpeechManifestConflictError("speech manifest contract is invalid") from exc


def _written(path: str, content: bytes) -> WrittenSpeechManifest:
    try:
        manifest = deserialize_speech_manifest(content)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SpeechManifestCorruptError("speech manifest is invalid") from exc
    return WrittenSpeechManifest(
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
