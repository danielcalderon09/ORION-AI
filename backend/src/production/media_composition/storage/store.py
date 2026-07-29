"""Atomic write-once plan and CAS manifest storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.media_composition.domain.models import (
    MediaCompositionManifest,
    MediaCompositionPlan,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionConflictError,
    MediaCompositionCorruptError,
)
from backend.src.production.media_composition.paths import (
    media_composition_manifest_relative_path,
    media_composition_plan_relative_path,
)
from backend.src.production.media_composition.ports import (
    MediaCompositionStageContext,
)
from backend.src.production.media_composition.serialization import (
    deserialize_media_composition_manifest,
    deserialize_media_composition_plan,
    serialize_media_composition_manifest,
    serialize_media_composition_plan,
)


class LocalMediaCompositionStore:
    def __init__(
        self,
        workspace_root: Path,
        *,
        max_plan_bytes: int,
        max_manifest_bytes: int,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._max_plan_bytes = max_plan_bytes
        self._max_manifest_bytes = max_manifest_bytes

    async def read_plan(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionPlan | None:
        return await asyncio.to_thread(self._read_plan_sync, context)

    async def write_plan(
        self,
        *,
        context: MediaCompositionStageContext,
        plan: MediaCompositionPlan,
    ) -> tuple[str, int, str]:
        return await asyncio.to_thread(self._write_plan_sync, context, plan)

    async def read_manifest(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionManifest | None:
        return await asyncio.to_thread(self._read_manifest_sync, context)

    async def create_manifest(
        self,
        *,
        context: MediaCompositionStageContext,
        manifest: MediaCompositionManifest,
    ) -> None:
        await asyncio.to_thread(self._create_manifest_sync, context, manifest)

    async def checkpoint_manifest(
        self,
        *,
        context: MediaCompositionStageContext,
        previous: MediaCompositionManifest,
        current: MediaCompositionManifest,
    ) -> None:
        await asyncio.to_thread(
            self._checkpoint_manifest_sync,
            context,
            previous,
            current,
        )

    def _read_plan_sync(
        self,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionPlan | None:
        target = self._target(media_composition_plan_relative_path(context))
        if not target.exists():
            return None
        return deserialize_media_composition_plan(self._read_limited(target, self._max_plan_bytes))

    def _write_plan_sync(
        self,
        context: MediaCompositionStageContext,
        plan: MediaCompositionPlan,
    ) -> tuple[str, int, str]:
        relative_path = media_composition_plan_relative_path(context)
        target = self._target(relative_path)
        content = serialize_media_composition_plan(plan)
        self._check_size(content, self._max_plan_bytes, "plan")
        self._prepare_parent(target)
        with self._lock(target):
            if target.exists():
                existing = self._read_limited(target, self._max_plan_bytes)
                if existing != content:
                    raise MediaCompositionConflictError("existing composition plan differs")
                deserialize_media_composition_plan(existing)
            else:
                self._replace(target, content)
        return relative_path, len(content), hashlib.sha256(content).hexdigest()

    def _read_manifest_sync(
        self,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionManifest | None:
        target = self._target(media_composition_manifest_relative_path(context))
        if not target.exists():
            return None
        return deserialize_media_composition_manifest(
            self._read_limited(target, self._max_manifest_bytes)
        )

    def _create_manifest_sync(
        self,
        context: MediaCompositionStageContext,
        manifest: MediaCompositionManifest,
    ) -> None:
        target = self._target(media_composition_manifest_relative_path(context))
        content = serialize_media_composition_manifest(manifest)
        self._check_size(content, self._max_manifest_bytes, "manifest")
        self._prepare_parent(target)
        with self._lock(target):
            if target.exists():
                existing = self._read_limited(target, self._max_manifest_bytes)
                if existing != content:
                    raise MediaCompositionConflictError("composition manifest already exists")
                return
            self._replace(target, content)

    def _checkpoint_manifest_sync(
        self,
        context: MediaCompositionStageContext,
        previous: MediaCompositionManifest,
        current: MediaCompositionManifest,
    ) -> None:
        _validate_manifest_identity(previous, current)
        target = self._target(media_composition_manifest_relative_path(context))
        content = serialize_media_composition_manifest(current)
        self._check_size(content, self._max_manifest_bytes, "manifest")
        with self._lock(target):
            existing = self._read_limited(target, self._max_manifest_bytes)
            if existing != serialize_media_composition_manifest(previous):
                raise MediaCompositionConflictError("composition manifest CAS conflict")
            self._replace(target, content)

    def _target(self, relative_path: str) -> Path:
        try:
            return self._confinement.resolve(relative_path)
        except Exception as exc:
            raise MediaCompositionCorruptError("composition storage path is unsafe") from exc

    def _prepare_parent(self, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target)
        except (BinaryAssetError, OSError) as exc:
            raise MediaCompositionConflictError("composition directory cannot be prepared") from exc

    def _read_limited(self, target: Path, maximum: int) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > maximum:
                raise MediaCompositionCorruptError("composition JSON exceeds its configured limit")
            with target.open("rb") as stream:
                content = stream.read(maximum + 1)
        except MediaCompositionCorruptError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise MediaCompositionCorruptError("composition JSON cannot be read") from exc
        if len(content) > maximum:
            raise MediaCompositionCorruptError("composition JSON exceeds its configured limit")
        return content

    @staticmethod
    def _check_size(content: bytes, maximum: int, name: str) -> None:
        if len(content) > maximum:
            raise MediaCompositionConflictError(f"composition {name} exceeds configured limit")

    @contextmanager
    def _lock(self, target: Path) -> Iterator[None]:
        lock = target.with_suffix(target.suffix + ".lock")
        try:
            descriptor = os.open(
                lock,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
        except FileExistsError as exc:
            raise MediaCompositionConflictError("composition storage is locked") from exc
        try:
            yield
        finally:
            with suppress(FileNotFoundError):
                lock.unlink()

    @staticmethod
    def _replace(target: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            try:
                directory_descriptor = os.open(target.parent, os.O_RDONLY)
            except OSError:
                directory_descriptor = None
            if directory_descriptor is not None:
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()


def _validate_manifest_identity(
    previous: MediaCompositionManifest,
    current: MediaCompositionManifest,
) -> None:
    immutable = (
        "schema_version",
        "plan_version",
        "job_id",
        "stage",
        "attempt_number",
        "source_fingerprint",
        "plan_fingerprint",
        "timeline_checksum",
        "plan_relative_path",
        "plan_sha256",
        "plan_size_bytes",
        "generated_at",
    )
    if any(getattr(previous, name) != getattr(current, name) for name in immutable):
        raise MediaCompositionConflictError("composition manifest identity changed")
    if current.updated_at < previous.updated_at:
        raise MediaCompositionConflictError("composition manifest time moved backward")
