"""Atomic write-once request and CAS execution-manifest storage."""

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
from backend.src.production.rendering.exceptions import (
    RenderingConflictError,
    RenderingCorruptError,
)
from backend.src.production.rendering.models import (
    LocalRenderRequest,
    RenderExecutionManifest,
)
from backend.src.production.rendering.paths import (
    local_render_request_relative_path,
    render_execution_manifest_relative_path,
)
from backend.src.production.rendering.ports import RenderStageContext
from backend.src.production.rendering.serialization import (
    deserialize_local_render_request,
    deserialize_render_execution_manifest,
    serialize_local_render_request,
    serialize_render_execution_manifest,
)


class LocalRenderPreparationStore:
    def __init__(
        self,
        workspace_root: Path,
        *,
        max_request_bytes: int,
        max_manifest_bytes: int,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._max_request_bytes = max_request_bytes
        self._max_manifest_bytes = max_manifest_bytes

    async def read_request(
        self,
        *,
        context: RenderStageContext,
    ) -> LocalRenderRequest | None:
        return await asyncio.to_thread(self._read_request_sync, context)

    async def write_request(
        self,
        *,
        context: RenderStageContext,
        request: LocalRenderRequest,
    ) -> tuple[str, int, str]:
        return await asyncio.to_thread(self._write_request_sync, context, request)

    async def read_manifest(
        self,
        *,
        context: RenderStageContext,
    ) -> RenderExecutionManifest | None:
        return await asyncio.to_thread(self._read_manifest_sync, context)

    async def create_manifest(
        self,
        *,
        context: RenderStageContext,
        manifest: RenderExecutionManifest,
    ) -> None:
        await asyncio.to_thread(self._create_manifest_sync, context, manifest)

    async def checkpoint_manifest(
        self,
        *,
        context: RenderStageContext,
        previous: RenderExecutionManifest,
        current: RenderExecutionManifest,
    ) -> None:
        await asyncio.to_thread(
            self._checkpoint_manifest_sync,
            context,
            previous,
            current,
        )

    async def output_exists(self, *, relative_path: str) -> bool:
        return await asyncio.to_thread(self._output_exists_sync, relative_path)

    def _read_request_sync(
        self,
        context: RenderStageContext,
    ) -> LocalRenderRequest | None:
        target = self._target(local_render_request_relative_path(context))
        if not target.exists():
            return None
        return deserialize_local_render_request(self._read_limited(target, self._max_request_bytes))

    def _write_request_sync(
        self,
        context: RenderStageContext,
        request: LocalRenderRequest,
    ) -> tuple[str, int, str]:
        relative_path = local_render_request_relative_path(context)
        target = self._target(relative_path)
        content = serialize_local_render_request(request)
        self._check_size(content, self._max_request_bytes, "request")
        self._prepare_parent(target)
        with self._lock(target):
            if target.exists():
                existing = self._read_limited(target, self._max_request_bytes)
                if existing != content:
                    raise RenderingConflictError("existing local render request differs")
                deserialize_local_render_request(existing)
            else:
                self._replace(target, content)
        return relative_path, len(content), hashlib.sha256(content).hexdigest()

    def _read_manifest_sync(
        self,
        context: RenderStageContext,
    ) -> RenderExecutionManifest | None:
        target = self._target(render_execution_manifest_relative_path(context))
        if not target.exists():
            return None
        return deserialize_render_execution_manifest(
            self._read_limited(target, self._max_manifest_bytes)
        )

    def _create_manifest_sync(
        self,
        context: RenderStageContext,
        manifest: RenderExecutionManifest,
    ) -> None:
        target = self._target(render_execution_manifest_relative_path(context))
        content = serialize_render_execution_manifest(manifest)
        self._check_size(content, self._max_manifest_bytes, "manifest")
        self._prepare_parent(target)
        with self._lock(target):
            if target.exists():
                existing = self._read_limited(target, self._max_manifest_bytes)
                if existing != content:
                    raise RenderingConflictError("render execution manifest already exists")
                deserialize_render_execution_manifest(existing)
                return
            self._replace(target, content)

    def _checkpoint_manifest_sync(
        self,
        context: RenderStageContext,
        previous: RenderExecutionManifest,
        current: RenderExecutionManifest,
    ) -> None:
        _validate_manifest_checkpoint(previous, current)
        target = self._target(render_execution_manifest_relative_path(context))
        content = serialize_render_execution_manifest(current)
        self._check_size(content, self._max_manifest_bytes, "manifest")
        with self._lock(target):
            existing = self._read_limited(target, self._max_manifest_bytes)
            if existing != serialize_render_execution_manifest(previous):
                raise RenderingConflictError("render execution manifest CAS conflict")
            self._replace(target, content)

    def _output_exists_sync(self, relative_path: str) -> bool:
        target = self._target(relative_path)
        if not target.exists():
            return False
        try:
            self._confinement.reject_unsafe_file(target)
        except BinaryAssetError as exc:
            raise RenderingCorruptError("future render output path is unsafe") from exc
        return True

    def _target(self, relative_path: str) -> Path:
        try:
            return self._confinement.resolve(relative_path)
        except Exception as exc:
            raise RenderingCorruptError("render storage path is unsafe") from exc

    def _prepare_parent(self, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target)
        except (BinaryAssetError, OSError) as exc:
            raise RenderingConflictError("render preparation directory cannot be prepared") from exc

    def _read_limited(self, target: Path, maximum: int) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > maximum:
                raise RenderingCorruptError("render JSON exceeds its configured limit")
            with target.open("rb") as stream:
                content = stream.read(maximum + 1)
        except RenderingCorruptError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise RenderingCorruptError("render JSON cannot be read") from exc
        if len(content) > maximum:
            raise RenderingCorruptError("render JSON exceeds its configured limit")
        return content

    @staticmethod
    def _check_size(content: bytes, maximum: int, label: str) -> None:
        if len(content) > maximum:
            raise RenderingConflictError(f"render {label} exceeds configured limit")

    @contextmanager
    def _lock(self, target: Path) -> Iterator[None]:
        lock = target.with_suffix(target.suffix + ".lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise RenderingConflictError("render preparation storage is locked") from exc
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


def _validate_manifest_checkpoint(
    previous: RenderExecutionManifest,
    current: RenderExecutionManifest,
) -> None:
    immutable = (
        "schema_version",
        "job_id",
        "stage",
        "attempt_number",
        "renderer_kind",
        "renderer_version",
        "source_plan_artifact_id",
        "source_plan_relative_path",
        "source_plan_sha256",
        "source_plan_fingerprint",
        "timeline_checksum",
        "request_fingerprint",
        "requested_output",
        "capabilities_fingerprint",
        "output_artifact_id",
        "output_relative_path",
        "output_sha256",
        "output_size_bytes",
        "media_produced",
        "created_at",
    )
    if any(getattr(previous, name) != getattr(current, name) for name in immutable):
        raise RenderingConflictError("render execution manifest identity changed")
    if current.updated_at < previous.updated_at:
        raise RenderingConflictError("render execution manifest time moved backward")
    allowed = {
        ("prepared", "validating"),
        ("validating", "validated"),
        ("validating", "invalid"),
        ("validating", "failed"),
    }
    if (
        previous.status != current.status
        and (previous.status.value, current.status.value) not in allowed
    ):
        raise RenderingConflictError("render execution manifest transition is invalid")
