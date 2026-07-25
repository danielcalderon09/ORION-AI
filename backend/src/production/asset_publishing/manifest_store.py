"""Atomic durable storage for published-assets-manifest.json."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID

from backend.src.production.asset_publishing.exceptions import (
    PublishedAssetManifestConflictError,
    PublishedAssetManifestCorruptError,
    PublishedAssetManifestError,
)
from backend.src.production.asset_publishing.models import (
    PublishedAssetManifest,
    validate_published_manifest_transition,
)
from backend.src.production.asset_publishing.serialization import (
    deserialize_published_asset_manifest,
    serialize_published_asset_manifest,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement

_ATTEMPT = re.compile(r"^attempt-([1-9][0-9]*)$")


def published_asset_manifest_relative_path(
    *, job_id: UUID, attempt_number: int
) -> str:
    if attempt_number < 1:
        raise ValueError("publication attempt must be positive")
    return PurePosixPath(
        "production",
        str(job_id),
        "asset_publishing",
        f"attempt-{attempt_number}",
        "published-assets-manifest.json",
    ).as_posix()


class InMemoryPublishedAssetManifestStore:
    def __init__(self) -> None:
        self.manifests: dict[tuple[UUID, int], PublishedAssetManifest] = {}
        self.checkpoints = 0

    async def read(
        self, *, job_id: UUID, attempt_number: int
    ) -> PublishedAssetManifest | None:
        return self.manifests.get((job_id, attempt_number))

    async def create(self, manifest: PublishedAssetManifest) -> None:
        key = (manifest.job_id, manifest.attempt_number)
        if key in self.manifests:
            raise PublishedAssetManifestConflictError(
                "published manifest already exists"
            )
        self.manifests[key] = manifest
        self.checkpoints += 1

    async def checkpoint(
        self,
        *,
        previous: PublishedAssetManifest,
        current: PublishedAssetManifest,
    ) -> None:
        try:
            validate_published_manifest_transition(previous, current)
        except ValueError as exc:
            raise PublishedAssetManifestConflictError(
                "published manifest transition is invalid"
            ) from exc
        key = (previous.job_id, previous.attempt_number)
        if self.manifests.get(key) != previous:
            raise PublishedAssetManifestConflictError(
                "published manifest changed concurrently"
            )
        self.manifests[key] = current
        self.checkpoints += 1

    async def list_manifests(self) -> tuple[PublishedAssetManifest, ...]:
        return tuple(
            self.manifests[key]
            for key in sorted(self.manifests, key=lambda item: (str(item[0]), item[1]))
        )


class LocalPublishedAssetManifestStore:
    def __init__(self, workspace: Path, *, max_bytes: int = 4_000_000) -> None:
        if not 1 <= max_bytes <= 16_000_000:
            raise ValueError("published manifest maximum is outside safe limits")
        self._root = workspace
        self._confinement = WorkspaceConfinement(workspace)
        self._maximum = max_bytes

    async def read(
        self, *, job_id: UUID, attempt_number: int
    ) -> PublishedAssetManifest | None:
        return await asyncio.to_thread(self._read_sync, job_id, attempt_number)

    async def create(self, manifest: PublishedAssetManifest) -> None:
        await asyncio.to_thread(self._create_sync, manifest)

    async def checkpoint(
        self,
        *,
        previous: PublishedAssetManifest,
        current: PublishedAssetManifest,
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, previous, current)

    async def list_manifests(self) -> tuple[PublishedAssetManifest, ...]:
        return await asyncio.to_thread(self._list_sync)

    def _read_sync(
        self, job_id: UUID, attempt_number: int
    ) -> PublishedAssetManifest | None:
        target = self._target(job_id, attempt_number)
        if not target.exists() and not target.is_symlink():
            return None
        return self._read_file(target)

    def _create_sync(self, manifest: PublishedAssetManifest) -> None:
        target = self._target(manifest.job_id, manifest.attempt_number)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(target.parent)
        with self._lock(target):
            if target.exists() or target.is_symlink():
                raise PublishedAssetManifestConflictError(
                    "published manifest already exists"
                )
            self._replace(target, serialize_published_asset_manifest(manifest))

    def _checkpoint_sync(
        self,
        previous: PublishedAssetManifest,
        current: PublishedAssetManifest,
    ) -> None:
        try:
            validate_published_manifest_transition(previous, current)
        except ValueError as exc:
            raise PublishedAssetManifestConflictError(
                "published manifest transition is invalid"
            ) from exc
        target = self._target(previous.job_id, previous.attempt_number)
        with self._lock(target):
            if self._read_file(target) != previous:
                raise PublishedAssetManifestConflictError(
                    "published manifest changed concurrently"
                )
            self._replace(target, serialize_published_asset_manifest(current))

    def _list_sync(self) -> tuple[PublishedAssetManifest, ...]:
        production = self._root / "production"
        if not production.exists():
            return ()
        result: list[PublishedAssetManifest] = []
        for job_directory in sorted(production.iterdir(), key=lambda item: item.name):
            try:
                job_id = UUID(job_directory.name)
            except ValueError:
                continue
            publishing = job_directory / "asset_publishing"
            if not publishing.exists():
                continue
            self._confinement.reject_unsafe_components(publishing)
            for attempt_directory in sorted(
                publishing.iterdir(), key=lambda item: item.name
            ):
                match = _ATTEMPT.fullmatch(attempt_directory.name)
                if match is None:
                    continue
                target = attempt_directory / "published-assets-manifest.json"
                if target.exists() or target.is_symlink():
                    manifest = self._read_file(target)
                    if (
                        manifest.job_id != job_id
                        or manifest.attempt_number != int(match.group(1))
                    ):
                        raise PublishedAssetManifestCorruptError(
                            "published manifest path identity differs"
                        )
                    result.append(manifest)
        return tuple(result)

    def _target(self, job_id: UUID, attempt_number: int) -> Path:
        relative = published_asset_manifest_relative_path(
            job_id=job_id,
            attempt_number=attempt_number,
        )
        return self._confinement.resolve(relative)

    def _read_file(self, target: Path) -> PublishedAssetManifest:
        try:
            self._confinement.reject_unsafe_file(target)
            content = target.read_bytes()
            if not content or len(content) > self._maximum:
                raise PublishedAssetManifestCorruptError(
                    "published manifest size is invalid"
                )
            return deserialize_published_asset_manifest(content)
        except PublishedAssetManifestError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise PublishedAssetManifestCorruptError(
                "published manifest is invalid"
            ) from exc

    def _replace(self, target: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise PublishedAssetManifestError("published manifest exceeds safe limit")
        descriptor = -1
        temporary: Path | None = None
        try:
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
        except OSError as exc:
            raise PublishedAssetManifestError(
                "published manifest could not be persisted"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        self._confinement.reject_unsafe_components(lock)
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PublishedAssetManifestConflictError(
                "published manifest is locked"
            ) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise PublishedAssetManifestError(
                    "published manifest lock could not be released"
                ) from exc


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
