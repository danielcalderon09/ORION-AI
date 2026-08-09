"""Development-only filesystem publisher with durable publication sidecars."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationConflictError,
    AssetPublicationExpiredError,
    AssetPublicationIntegrityError,
    AssetPublicationNotFoundError,
    AssetPublishingConfigurationError,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
)
from backend.src.production.asset_publishing.serialization import (
    _reject_constant,
    _reject_duplicates,
)
from backend.src.production.asset_publishing.url_validation import (
    public_url_hash,
    validate_public_https_url,
)
from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement

_PUBLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "video/mp4": "mp4",
}


class FilesystemPublisher:
    """Copies verified bytes into a dedicated development publication root."""

    name = "filesystem"

    def __init__(
        self,
        *,
        public_root: Path,
        public_base_url: str,
        max_asset_bytes: int = 250_000_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 1 <= max_asset_bytes <= 250_000_000:
            raise AssetPublishingConfigurationError("filesystem publication size limit is invalid")
        self._root = public_root
        self._confinement = WorkspaceConfinement(public_root)
        self._base_url = validate_public_https_url(public_base_url.rstrip("/"))
        self._maximum = max_asset_bytes
        self._clock = clock
        self._closed = False

    async def publish(
        self,
        *,
        asset: PublishableAsset,
        expires_at: datetime,
    ) -> AssetPublicationReceipt:
        return await asyncio.to_thread(self._publish_sync, asset, expires_at)

    async def delete(self, *, asset: PublishedAsset) -> None:
        await asyncio.to_thread(self._delete_sync, asset)

    async def exists(self, *, asset: PublishedAsset) -> bool:
        return await asyncio.to_thread(self._exists_sync, asset)

    async def get_public_url(self, *, asset: PublishedAsset) -> str:
        receipt = await asyncio.to_thread(self._receipt_for_asset, asset)
        if receipt.expires_at <= self._aware_now():
            raise AssetPublicationExpiredError("published asset URL has expired")
        return receipt.public_url

    async def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("cleanup time must be timezone-aware")
        return await asyncio.to_thread(self._cleanup_sync, now)

    async def close(self) -> None:
        self._closed = True

    def _publish_sync(
        self, asset: PublishableAsset, expires_at: datetime
    ) -> AssetPublicationReceipt:
        if self._closed:
            raise AssetPublishingConfigurationError("filesystem publisher is closed")
        now = self._aware_now()
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("publication expiry must be timezone-aware")
        if expires_at <= now:
            raise AssetPublicationExpiredError("publication expiry is not in the future")
        if asset.size_bytes > self._maximum:
            raise AssetPublicationIntegrityError("asset exceeds filesystem publication limit")
        publication_id = _publication_id(asset)
        extension = _extension(asset.content_type)
        target = self._target(publication_id, extension)
        sidecar = self._sidecar(publication_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(target.parent)
        public_url = validate_public_https_url(
            f"{self._base_url}/assets/{publication_id}.{extension}"
        )
        receipt = AssetPublicationReceipt(
            publication_id=publication_id,
            public_url=public_url,
            url_hash=public_url_hash(public_url),
            published_at=now,
            expires_at=expires_at,
            publisher=self.name,
            source_hash=asset.source_hash,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
        )
        with self._lock(publication_id):
            if target.exists() or sidecar.exists():
                if target.exists() and sidecar.exists():
                    existing = self._read_receipt(sidecar)
                    self._validate_existing(target, existing, asset)
                    if existing != receipt:
                        self._atomic_replace(sidecar, _serialize_receipt(receipt))
                        return receipt
                    return existing
                if target.exists():
                    self._validate_file(
                        target,
                        source_hash=asset.source_hash,
                        size_bytes=asset.size_bytes,
                    )
                    self._atomic_write(sidecar, _serialize_receipt(receipt))
                    return receipt
                existing = self._read_receipt(sidecar)
                self._validate_receipt_source(existing, asset)
                self._atomic_write(target, asset.content)
                if existing != receipt:
                    self._atomic_replace(sidecar, _serialize_receipt(receipt))
                    return receipt
                return existing
            self._atomic_write(target, asset.content)
            try:
                self._atomic_write(sidecar, _serialize_receipt(receipt))
            except Exception:
                target.unlink(missing_ok=True)
                raise
        return receipt

    def _delete_sync(self, asset: PublishedAsset) -> None:
        publication_id = asset.metadata.publication_id
        extension = _extension(asset.content_type)
        target = self._target(publication_id, extension)
        sidecar = self._sidecar(publication_id)
        if (
            not target.exists()
            and not target.is_symlink()
            and not sidecar.exists()
            and not sidecar.is_symlink()
        ):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock(publication_id):
            self._safe_unlink(target)
            self._safe_unlink(sidecar)

    def _exists_sync(self, asset: PublishedAsset) -> bool:
        try:
            receipt = self._receipt_for_asset(asset)
            target = self._target(
                asset.metadata.publication_id,
                _extension(asset.content_type),
            )
            self._validate_file(
                target,
                source_hash=asset.source_hash,
                size_bytes=asset.size_bytes,
            )
            return receipt.source_hash == asset.source_hash
        except (
            AssetPublicationNotFoundError,
            AssetPublicationIntegrityError,
            BinaryAssetError,
            OSError,
        ):
            return False

    def _receipt_for_asset(self, asset: PublishedAsset) -> AssetPublicationReceipt:
        receipt = self._read_receipt(self._sidecar(asset.metadata.publication_id))
        if (
            receipt.source_hash != asset.source_hash
            or receipt.content_type != asset.content_type
            or receipt.size_bytes != asset.size_bytes
            or receipt.publisher != self.name
        ):
            raise AssetPublicationIntegrityError(
                "publication sidecar differs from durable manifest"
            )
        return receipt

    def _cleanup_sync(self, now: datetime) -> tuple[str, ...]:
        if not self._root.exists():
            return ()
        self._confinement.reject_unsafe_components(self._root)
        removed: list[str] = []
        for sidecar in sorted(self._root.glob("*.publication.json")):
            receipt = self._read_receipt(sidecar)
            if receipt.expires_at > now:
                continue
            extension = _extension(receipt.content_type)
            with self._lock(receipt.publication_id):
                self._safe_unlink(self._target(receipt.publication_id, extension))
                self._safe_unlink(sidecar)
            removed.append(receipt.publication_id)
        return tuple(removed)

    def _validate_existing(
        self,
        target: Path,
        receipt: AssetPublicationReceipt,
        asset: PublishableAsset,
    ) -> None:
        self._validate_receipt_source(receipt, asset)
        self._validate_file(
            target,
            source_hash=asset.source_hash,
            size_bytes=asset.size_bytes,
        )

    @staticmethod
    def _validate_receipt_source(
        receipt: AssetPublicationReceipt,
        asset: PublishableAsset,
    ) -> None:
        if (
            receipt.source_hash != asset.source_hash
            or receipt.content_type != asset.content_type
            or receipt.size_bytes != asset.size_bytes
        ):
            raise AssetPublicationConflictError("existing publication belongs to different content")

    def _validate_file(self, target: Path, *, source_hash: str, size_bytes: int) -> None:
        if not target.exists():
            raise AssetPublicationNotFoundError("published file is missing")
        try:
            self._confinement.reject_unsafe_file(target)
            with target.open("rb") as stream:
                content = stream.read(size_bytes + 1)
        except BinaryAssetError as exc:
            raise AssetPublicationIntegrityError("published file path is unsafe") from exc
        if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != source_hash:
            raise AssetPublicationIntegrityError("published file integrity differs")

    def _read_receipt(self, sidecar: Path) -> AssetPublicationReceipt:
        if not sidecar.exists():
            raise AssetPublicationNotFoundError("publication sidecar is missing")
        try:
            self._confinement.reject_unsafe_file(sidecar)
            with sidecar.open("rb") as stream:
                content = stream.read(64_001)
            if not content or len(content) > 64_000:
                raise ValueError("publication sidecar size is invalid")
            payload = json.loads(
                content.decode("utf-8", errors="strict"),
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicates,
            )
            return AssetPublicationReceipt.model_validate(payload)
        except (
            AssetPublicationNotFoundError,
            AssetPublicationIntegrityError,
        ):
            raise
        except (BinaryAssetError, OSError, UnicodeError, ValueError, TypeError) as exc:
            raise AssetPublicationIntegrityError("publication sidecar is invalid") from exc

    def _target(self, publication_id: str, extension: str) -> Path:
        _validate_publication_id(publication_id)
        return self._confinement.resolve(f"{publication_id}.{extension}")

    def _sidecar(self, publication_id: str) -> Path:
        _validate_publication_id(publication_id)
        return self._confinement.resolve(f"{publication_id}.publication.json")

    @contextmanager
    def _lock(self, publication_id: str) -> Iterator[None]:
        lock = self._confinement.resolve(f".{publication_id}.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AssetPublicationConflictError("asset publication is already in progress") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _atomic_write(self, target: Path, content: bytes) -> None:
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
            if target.exists() or target.is_symlink():
                raise AssetPublicationConflictError("publication target appeared concurrently")
            os.replace(temporary, target)
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _atomic_replace(self, target: Path, content: bytes) -> None:
        self._confinement.reject_unsafe_file(target)
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
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _safe_unlink(self, target: Path) -> None:
        if not target.exists() and not target.is_symlink():
            return
        self._confinement.reject_unsafe_file(target)
        target.unlink()
        _fsync_directory(target.parent)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AssetPublishingConfigurationError(
                "filesystem publisher clock must be timezone-aware"
            )
        return value


def _publication_id(asset: PublishableAsset) -> str:
    digest = hashlib.sha256(f"{asset.binary_asset_id}:{asset.source_hash}".encode()).hexdigest()[
        :32
    ]
    return f"pub-{digest}"


def _validate_publication_id(value: str) -> None:
    if _PUBLICATION_ID.fullmatch(value) is None:
        raise AssetPublicationIntegrityError("publication ID is invalid")


def _extension(content_type: str) -> str:
    try:
        return _EXTENSIONS[content_type]
    except KeyError as exc:
        raise AssetPublicationIntegrityError("publication content type is unsupported") from exc


def _serialize_receipt(receipt: AssetPublicationReceipt) -> bytes:
    return (
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


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
