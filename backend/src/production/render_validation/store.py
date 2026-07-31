"""Atomic CAS storage for final-render validation manifests."""

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
from backend.src.production.render_validation.exceptions import (
    FinalRenderConflictError,
    FinalRenderCorruptError,
)
from backend.src.production.render_validation.models import (
    FinalRenderValidationManifest,
    FinalValidationStatus,
)
from backend.src.production.render_validation.paths import (
    final_render_validation_relative_path,
)
from backend.src.production.render_validation.ports import FinalValidationStageContext
from backend.src.production.render_validation.serialization import (
    deserialize_final_render_validation,
    serialize_final_render_validation,
)


class LocalFinalRenderValidationStore:
    def __init__(self, *, workspace_root: Path, max_manifest_bytes: int) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_manifest_bytes

    async def read_manifest(
        self,
        *,
        context: FinalValidationStageContext,
    ) -> FinalRenderValidationManifest | None:
        return await asyncio.to_thread(self._read_manifest_sync, context)

    async def create_manifest(
        self,
        *,
        context: FinalValidationStageContext,
        manifest: FinalRenderValidationManifest,
    ) -> tuple[str, int, str]:
        return await asyncio.to_thread(self._create_sync, context, manifest)

    async def checkpoint_manifest(
        self,
        *,
        context: FinalValidationStageContext,
        previous: FinalRenderValidationManifest,
        current: FinalRenderValidationManifest,
    ) -> tuple[str, int, str]:
        return await asyncio.to_thread(self._checkpoint_sync, context, previous, current)

    async def manifest_identity(
        self,
        *,
        context: FinalValidationStageContext,
    ) -> tuple[str, int, str]:
        return await asyncio.to_thread(self._manifest_identity_sync, context)

    async def media_identity(self, *, relative_path: str) -> tuple[int, str]:
        return await asyncio.to_thread(self._media_identity_sync, relative_path)

    def _read_manifest_sync(
        self,
        context: FinalValidationStageContext,
    ) -> FinalRenderValidationManifest | None:
        target = self._target(final_render_validation_relative_path(context))
        if not target.exists():
            return None
        return deserialize_final_render_validation(self._read_limited(target))

    def _create_sync(
        self,
        context: FinalValidationStageContext,
        manifest: FinalRenderValidationManifest,
    ) -> tuple[str, int, str]:
        relative = final_render_validation_relative_path(context)
        target = self._target(relative)
        content = serialize_final_render_validation(manifest)
        self._check_size(content)
        self._prepare_parent(target)
        with self._lock(target):
            if target.exists():
                existing = self._read_limited(target)
                if existing != content:
                    raise FinalRenderConflictError(
                        "validation_manifest_exists",
                        "final-render validation manifest already differs",
                    )
                deserialize_final_render_validation(existing)
            else:
                self._replace(target, content)
        return relative, len(content), hashlib.sha256(content).hexdigest()

    def _checkpoint_sync(
        self,
        context: FinalValidationStageContext,
        previous: FinalRenderValidationManifest,
        current: FinalRenderValidationManifest,
    ) -> tuple[str, int, str]:
        _validate_checkpoint(previous, current)
        relative = final_render_validation_relative_path(context)
        target = self._target(relative)
        content = serialize_final_render_validation(current)
        self._check_size(content)
        with self._lock(target):
            existing = self._read_limited(target)
            if existing != serialize_final_render_validation(previous):
                raise FinalRenderConflictError(
                    "validation_manifest_cas_conflict",
                    "final-render validation manifest changed concurrently",
                )
            self._replace(target, content)
        return relative, len(content), hashlib.sha256(content).hexdigest()

    def _manifest_identity_sync(
        self,
        context: FinalValidationStageContext,
    ) -> tuple[str, int, str]:
        relative = final_render_validation_relative_path(context)
        content = self._read_limited(self._target(relative))
        deserialize_final_render_validation(content)
        return relative, len(content), hashlib.sha256(content).hexdigest()

    def _media_identity_sync(self, relative_path: str) -> tuple[int, str]:
        target = self._target(relative_path, require_exists=True)
        try:
            self._confinement.reject_unsafe_file(target)
            status = target.stat()
            if status.st_size <= 0 or status.st_nlink != 1:
                raise FinalRenderCorruptError(
                    "render_file_invalid",
                    "final render is empty or linked",
                )
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                while chunk := stream.read(1_048_576):
                    digest.update(chunk)
        except FinalRenderCorruptError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise FinalRenderCorruptError(
                "render_file_missing",
                "final render is missing or unsafe",
            ) from exc
        return status.st_size, digest.hexdigest()

    def _target(self, relative_path: str, *, require_exists: bool = False) -> Path:
        try:
            return self._confinement.resolve(
                relative_path,
                require_exists=require_exists,
            )
        except Exception as exc:
            raise FinalRenderCorruptError(
                "validation_path_unsafe",
                "final-render validation path is unsafe",
            ) from exc

    def _read_limited(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise FinalRenderCorruptError(
                    "validation_manifest_oversized",
                    "final-render validation manifest exceeds its limit",
                )
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except FinalRenderCorruptError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise FinalRenderCorruptError(
                "validation_manifest_unreadable",
                "final-render validation manifest cannot be read",
            ) from exc
        if len(content) > self._maximum:
            raise FinalRenderCorruptError(
                "validation_manifest_oversized",
                "final-render validation manifest exceeds its limit",
            )
        return content

    def _prepare_parent(self, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target.parent)
        except (BinaryAssetError, OSError) as exc:
            raise FinalRenderConflictError(
                "validation_directory_unsafe",
                "final-render validation directory cannot be prepared",
            ) from exc

    def _check_size(self, content: bytes) -> None:
        if len(content) > self._maximum:
            raise FinalRenderConflictError(
                "validation_manifest_oversized",
                "final-render validation manifest exceeds its limit",
            )

    @contextmanager
    def _lock(self, target: Path) -> Iterator[None]:
        lock = target.with_suffix(target.suffix + ".lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise FinalRenderConflictError(
                "validation_manifest_locked",
                "final-render validation manifest is locked",
            ) from exc
        try:
            yield
        finally:
            with suppress(FileNotFoundError):
                lock.unlink()

    @staticmethod
    def _replace(target: Path, content: bytes) -> None:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_checkpoint(
    previous: FinalRenderValidationManifest,
    current: FinalRenderValidationManifest,
) -> None:
    immutable = ("schema_version", "job_id", "stage", "attempt_number", "created_at")
    if any(getattr(previous, name) != getattr(current, name) for name in immutable):
        raise FinalRenderConflictError(
            "validation_identity_changed",
            "final-render validation identity changed",
        )
    allowed = {
        (FinalValidationStatus.PREPARED, FinalValidationStatus.VALIDATING),
        (FinalValidationStatus.PREPARED, FinalValidationStatus.FAILED),
        (FinalValidationStatus.VALIDATING, FinalValidationStatus.VALIDATED),
        (FinalValidationStatus.VALIDATING, FinalValidationStatus.FAILED),
    }
    if previous.status != current.status and (previous.status, current.status) not in allowed:
        raise FinalRenderConflictError(
            "validation_transition_invalid",
            "final-render validation state transition is invalid",
        )
    if current.updated_at < previous.updated_at:
        raise FinalRenderConflictError(
            "validation_time_regressed",
            "final-render validation time moved backward",
        )
