"""Atomic compare-and-swap manifest checkpoints for image acquisition."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

from pydantic import Field

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionManifestConflictException,
    ImageAcquisitionManifestCorruptException,
)
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionManifestStatus,
    ProductionImageAcquisitionManifest,
    validate_manifest_transition,
)
from backend.src.production.image_acquisition.serialization import (
    deserialize_image_acquisition_manifest,
    serialize_image_acquisition_manifest,
)
from backend.src.production.runtime.context import StageContext


class WrittenImageAcquisitionManifest(ContractModel):
    relative_path: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest: ProductionImageAcquisitionManifest


class InMemoryImageAcquisitionManifestWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}
        self.checkpoint_count = 0

    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> ProductionImageAcquisitionManifest | None:
        content = self.contents.get(image_acquisition_manifest_relative_path(context))
        return (
            deserialize_image_acquisition_manifest(content)
            if content is not None
            else None
        )

    async def create(
        self,
        *,
        context: StageContext,
        manifest: ProductionImageAcquisitionManifest,
    ) -> None:
        path = image_acquisition_manifest_relative_path(context)
        if path in self.contents:
            raise ImageAcquisitionManifestConflictException(
                "image acquisition manifest already exists"
            )
        self.contents[path] = serialize_image_acquisition_manifest(manifest)
        self.checkpoint_count += 1

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None:
        validate_manifest_transition(previous, current)
        path = image_acquisition_manifest_relative_path(context)
        if self.contents.get(path) != serialize_image_acquisition_manifest(previous):
            raise ImageAcquisitionManifestConflictException(
                "image acquisition checkpoint changed concurrently"
            )
        self.contents[path] = serialize_image_acquisition_manifest(current)
        self.checkpoint_count += 1

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None:
        if current.status is not ImageAcquisitionManifestStatus.COMPLETED:
            raise ImageAcquisitionManifestConflictException(
                "final image acquisition manifest must be completed"
            )
        await self.checkpoint(
            context=context,
            previous=previous,
            current=current,
        )

    def written(
        self,
        *,
        context: StageContext,
    ) -> WrittenImageAcquisitionManifest:
        path = image_acquisition_manifest_relative_path(context)
        content = self.contents[path]
        return _written(path, content)


class LocalImageAcquisitionManifestWriter:
    def __init__(self, workspace_root: Path, *, max_manifest_bytes: int) -> None:
        if max_manifest_bytes < 1:
            raise ValueError("maximum image acquisition manifest size must be positive")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._max_bytes = max_manifest_bytes

    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> ProductionImageAcquisitionManifest | None:
        return await asyncio.to_thread(self._read_existing_sync, context)

    async def create(
        self,
        *,
        context: StageContext,
        manifest: ProductionImageAcquisitionManifest,
    ) -> None:
        await asyncio.to_thread(self._create_sync, context, manifest)

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None:
        await asyncio.to_thread(
            self._checkpoint_sync,
            context,
            previous,
            current,
        )

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None:
        if current.status is not ImageAcquisitionManifestStatus.COMPLETED:
            raise ImageAcquisitionManifestConflictException(
                "final image acquisition manifest must be completed"
            )
        await self.checkpoint(
            context=context,
            previous=previous,
            current=current,
        )

    async def written(
        self,
        *,
        context: StageContext,
    ) -> WrittenImageAcquisitionManifest:
        return await asyncio.to_thread(self._written_sync, context)

    def _read_existing_sync(
        self,
        context: StageContext,
    ) -> ProductionImageAcquisitionManifest | None:
        target = self._target(context)
        if not target.exists():
            return None
        return self._read_manifest(target)

    def _create_sync(
        self,
        context: StageContext,
        manifest: ProductionImageAcquisitionManifest,
    ) -> None:
        target = self._target(context)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(target)
        if target.exists() or target.is_symlink():
            raise ImageAcquisitionManifestConflictException(
                "image acquisition manifest already exists"
            )
        self._replace(target, serialize_image_acquisition_manifest(manifest))

    def _checkpoint_sync(
        self,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None:
        validate_manifest_transition(previous, current)
        target = self._target(context)
        if self._read_manifest(target) != previous:
            raise ImageAcquisitionManifestConflictException(
                "image acquisition checkpoint changed concurrently"
            )
        self._replace(target, serialize_image_acquisition_manifest(current))

    def _written_sync(
        self,
        context: StageContext,
    ) -> WrittenImageAcquisitionManifest:
        target = self._target(context)
        content = self._read_limited(target)
        return _written(image_acquisition_manifest_relative_path(context), content)

    def _target(self, context: StageContext) -> Path:
        try:
            return self._confinement.resolve(
                image_acquisition_manifest_relative_path(context)
            )
        except (BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise ImageAcquisitionManifestCorruptException(
                "image acquisition manifest path is unsafe"
            ) from exc

    def _read_manifest(self, target: Path) -> ProductionImageAcquisitionManifest:
        try:
            return deserialize_image_acquisition_manifest(self._read_limited(target))
        except ImageAcquisitionManifestCorruptException:
            raise
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ImageAcquisitionManifestCorruptException(
                "image acquisition manifest is invalid"
            ) from exc

    def _read_limited(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._max_bytes:
                raise ImageAcquisitionManifestCorruptException(
                    "image acquisition manifest exceeds the configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._max_bytes + 1)
        except ImageAcquisitionManifestCorruptException:
            raise
        except (BinaryAssetLinkError, BinaryAssetPathError, OSError) as exc:
            raise ImageAcquisitionManifestCorruptException(
                "image acquisition manifest could not be read safely"
            ) from exc
        if len(content) > self._max_bytes:
            raise ImageAcquisitionManifestCorruptException(
                "image acquisition manifest exceeds the configured limit"
            )
        return content

    def _replace(self, target: Path, content: bytes) -> None:
        if len(content) > self._max_bytes:
            raise ImageAcquisitionManifestConflictException(
                "image acquisition manifest exceeds the configured limit"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(target)
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
            self._confinement.reject_unsafe_components(target)
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except (BinaryAssetLinkError, BinaryAssetPathError, OSError):
            temporary.unlink(missing_ok=True)
            raise


def image_acquisition_manifest_relative_path(context: StageContext) -> str:
    expected = (
        f"production/{context.job_id}/acquiring_assets/"
        f"attempt-{context.attempt_number}/image-acquisition-manifest.json"
    )
    normalized = validate_relative_path(
        f"{context.workspace_relative_path}/image-acquisition-manifest.json"
    )
    if normalized != expected or "\\" in normalized:
        raise ImageAcquisitionManifestConflictException(
            "image acquisition manifest path is not contractual"
        )
    return normalized


def _written(
    relative_path: str,
    content: bytes,
) -> WrittenImageAcquisitionManifest:
    try:
        manifest = deserialize_image_acquisition_manifest(content)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ImageAcquisitionManifestCorruptException(
            "image acquisition manifest is invalid"
        ) from exc
    return WrittenImageAcquisitionManifest(
        relative_path=relative_path,
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
