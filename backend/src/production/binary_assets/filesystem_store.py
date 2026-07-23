"""Filesystem implementation of atomic binary asset storage."""

import asyncio
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetConflictError,
    BinaryAssetError,
    BinaryAssetIOError,
    BinaryAssetLinkError,
    BinaryAssetMetadataError,
    BinaryAssetNotFoundError,
)
from backend.src.production.binary_assets.models import (
    BinaryAssetWriteRequest,
    ProductionBinaryAsset,
    ProductionBinaryAssetReference,
    ReadProductionBinaryAsset,
    binary_asset_relative_path,
)
from backend.src.production.binary_assets.validators import (
    BinaryAssetIntegrityValidator,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement


class FilesystemBinaryAssetStore:
    """Write once and verify on every read; never trusts caller metadata alone."""

    def __init__(
        self,
        *,
        configuration: AssetStorageConfiguration,
        integrity_validator: BinaryAssetIntegrityValidator,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._configuration = configuration
        self._integrity = integrity_validator
        self._clock = clock
        self._confinement = WorkspaceConfinement(configuration.workspace)

    async def write(
        self,
        *,
        request: BinaryAssetWriteRequest,
        content: bytes,
    ) -> ProductionBinaryAsset:
        return await asyncio.to_thread(self._write_sync, request, content)

    async def read(
        self,
        *,
        reference: ProductionBinaryAssetReference,
    ) -> ReadProductionBinaryAsset:
        asset = await asyncio.to_thread(self._read_metadata_sync, reference)
        content = await asyncio.to_thread(self._read_bytes_sync, asset)
        return ReadProductionBinaryAsset(asset=asset, content=content)

    def _write_sync(
        self,
        request: BinaryAssetWriteRequest,
        content: bytes,
    ) -> ProductionBinaryAsset:
        sha256, size_bytes, inspected = self._integrity.validate_new(
            content,
            mime_type=request.mime_type,
            extension=request.extension,
        )
        if (
            request.expected_width is not None
            and request.expected_width != inspected.width
        ):
            raise BinaryAssetConflictError(
                "binary asset width differs from the requested contract"
            )
        if (
            request.expected_height is not None
            and request.expected_height != inspected.height
        ):
            raise BinaryAssetConflictError(
                "binary asset height differs from the requested contract"
            )
        relative_path = binary_asset_relative_path(
            job_id=request.job_id,
            asset_id=request.asset_id,
            extension=request.extension,
        )
        target = self._confinement.resolve(relative_path)
        metadata_target = self._metadata_target(relative_path)
        self._create_parent(target.parent)
        with self._exclusive_asset_lock(target):
            if target.exists() or metadata_target.exists():
                return self._reuse_or_conflict(
                    target=target,
                    metadata_target=metadata_target,
                    request=request,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    width=inspected.width,
                    height=inspected.height,
                )
            created_at = self._clock()
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError("binary asset clock must be timezone-aware")
            asset = ProductionBinaryAsset(
                asset_id=request.asset_id,
                job_id=request.job_id,
                scene_id=request.scene_id,
                shot_id=request.shot_id,
                asset_role=request.asset_role,
                mime_type=inspected.mime_type,
                extension=request.extension,
                sha256=sha256,
                size_bytes=size_bytes,
                width=inspected.width,
                height=inspected.height,
                created_at=created_at,
                storage_path=relative_path,
                metadata=request.metadata,
            )
            self._atomic_write(target=target, content=content)
            self._atomic_write(
                target=metadata_target,
                content=_serialize_asset(asset),
            )
            return asset

    def _reuse_or_conflict(
        self,
        *,
        target: Path,
        metadata_target: Path,
        request: BinaryAssetWriteRequest,
        sha256: str,
        size_bytes: int,
        width: int,
        height: int,
    ) -> ProductionBinaryAsset:
        if not target.exists() or not metadata_target.exists():
            raise BinaryAssetConflictError(
                "binary asset has incomplete durable metadata"
            )
        try:
            existing = self._read_metadata_file(metadata_target)
            expected_reference = ProductionBinaryAssetReference(
                asset_id=request.asset_id,
                job_id=request.job_id,
                storage_path=binary_asset_relative_path(
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                    extension=request.extension,
                ),
                mime_type=request.mime_type,
                extension=request.extension,
                sha256=sha256,
                size_bytes=size_bytes,
                width=width,
                height=height,
            )
            if ProductionBinaryAssetReference.from_asset(existing) != expected_reference:
                raise BinaryAssetConflictError(
                    "existing binary asset integrity metadata differs"
                )
            if (
                existing.scene_id != request.scene_id
                or existing.shot_id != request.shot_id
                or existing.asset_role != request.asset_role
                or existing.metadata != request.metadata
            ):
                raise BinaryAssetConflictError(
                    "existing binary asset descriptive metadata differs"
                )
            self._read_bytes_sync(existing)
        except BinaryAssetConflictError:
            raise
        except BinaryAssetError as exc:
            raise BinaryAssetConflictError(
                "existing binary asset is incompatible and was not overwritten"
            ) from exc
        return existing

    def _read_metadata_sync(
        self,
        reference: ProductionBinaryAssetReference,
    ) -> ProductionBinaryAsset:
        metadata_target = self._metadata_target(reference.storage_path)
        if not metadata_target.exists():
            raise BinaryAssetNotFoundError("binary asset metadata is missing")
        asset = self._read_metadata_file(metadata_target)
        if ProductionBinaryAssetReference.from_asset(asset) != reference:
            raise BinaryAssetMetadataError(
                "binary asset reference differs from durable metadata"
            )
        return asset

    def _read_metadata_file(self, target: Path) -> ProductionBinaryAsset:
        self._confinement.reject_unsafe_file(target)
        try:
            content = target.read_bytes()
            if len(content) > 64_000:
                raise BinaryAssetMetadataError(
                    "binary asset metadata exceeds the safe limit"
                )
            return deserialize_binary_asset_metadata(content)
        except BinaryAssetMetadataError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise BinaryAssetMetadataError(
                "binary asset metadata is invalid"
            ) from exc

    def _read_bytes_sync(self, asset: ProductionBinaryAsset) -> bytes:
        reference = ProductionBinaryAssetReference.from_asset(asset)
        target = self._confinement.resolve(
            reference.storage_path,
            require_exists=True,
        )
        self._confinement.reject_unsafe_file(target)
        try:
            with target.open("rb") as stream:
                content = stream.read(self._configuration.max_asset_size + 1)
                status = os.fstat(stream.fileno())
        except FileNotFoundError as exc:
            raise BinaryAssetNotFoundError("binary asset file is missing") from exc
        except OSError as exc:
            raise BinaryAssetIOError("binary asset could not be read") from exc
        if status.st_nlink != 1:
            raise BinaryAssetLinkError("binary asset hard links are not allowed")
        self._integrity.validate_existing(
            content,
            mime_type=reference.mime_type,
            extension=reference.extension,
            sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            width=reference.width,
            height=reference.height,
        )
        return content

    def _metadata_target(self, relative_path: str) -> Path:
        return self._confinement.resolve(f"{relative_path}.asset.json")

    @contextmanager
    def _exclusive_asset_lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        self._confinement.reject_unsafe_components(lock)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(lock, flags, 0o600)
        except FileExistsError as exc:
            raise BinaryAssetConflictError(
                "binary asset is already being written"
            ) from exc
        except OSError as exc:
            raise BinaryAssetIOError("binary asset lock could not be created") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise BinaryAssetIOError(
                    "binary asset lock could not be released"
                ) from exc

    def _create_parent(self, parent: Path) -> None:
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BinaryAssetIOError(
                "binary asset directory could not be created"
            ) from exc
        self._confinement.reject_unsafe_components(parent)

    def _atomic_write(self, *, target: Path, content: bytes) -> None:
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._confinement.reject_unsafe_components(target)
            if target.exists() or target.is_symlink():
                raise BinaryAssetConflictError(
                    "binary asset target appeared concurrently"
                )
            os.replace(temporary, target)
            temporary = None
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        except BinaryAssetConflictError:
            raise
        except OSError as exc:
            raise BinaryAssetIOError("binary asset could not be written") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)


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


def _serialize_asset(asset: ProductionBinaryAsset) -> bytes:
    return (
        json.dumps(
            asset.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def deserialize_binary_asset_metadata(content: bytes) -> ProductionBinaryAsset:
    """Strictly decode the durable metadata sidecar."""

    payload = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )
    return ProductionBinaryAsset.model_validate(payload)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON keys are not allowed")
        result[key] = value
    return result
