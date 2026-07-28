"""Write-once, workspace-confined storage for simulated music and SFX WAVs."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.src.production.audio_design.exceptions import (
    AudioDesignStoreConflictError,
    AudioDesignStoreIntegrityError,
    AudioDesignStoreNotFoundError,
    AudioDesignStorePathError,
    AudioDesignWavError,
)
from backend.src.production.audio_design.models import (
    AudioAssetExpectation,
    AudioAssetKind,
    AudioPcmMetadata,
    ReadStoredAudioDesignAsset,
    StoredAudioDesignAsset,
)
from backend.src.production.audio_design.serialization import (
    deserialize_audio_design_asset,
    serialize_audio_design_asset,
)
from backend.src.production.audio_design.wav import (
    AudioDesignWavValidator,
    AudioWavExpectations,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetNotFoundError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement


class FilesystemAudioDesignAssetStore:
    """Store one contractual audio kind under its dedicated asset directory."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        kind: AudioAssetKind,
        validator: AudioDesignWavValidator,
        max_audio_bytes: int,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._kind = kind
        self._validator = validator
        self._maximum = max_audio_bytes

    async def write(
        self,
        *,
        expectation: AudioAssetExpectation,
        content: bytes,
    ) -> StoredAudioDesignAsset:
        return await asyncio.to_thread(self._write_sync, expectation, content)

    async def recover(
        self,
        *,
        expectation: AudioAssetExpectation,
    ) -> StoredAudioDesignAsset | None:
        return await asyncio.to_thread(self._recover_sync, expectation)

    async def resolve(
        self,
        *,
        expectation: AudioAssetExpectation,
    ) -> ReadStoredAudioDesignAsset:
        asset = await asyncio.to_thread(self._resolve_sync, expectation)
        return await self.read(asset=asset)

    async def read(
        self,
        *,
        asset: StoredAudioDesignAsset,
    ) -> ReadStoredAudioDesignAsset:
        content = await asyncio.to_thread(self._read_asset_sync, asset)
        return ReadStoredAudioDesignAsset(asset=asset, content=content)

    def _write_sync(
        self,
        expectation: AudioAssetExpectation,
        content: bytes,
    ) -> StoredAudioDesignAsset:
        self._validate_kind(expectation)
        inspected = self._validate(content, expectation)
        expected = self._asset(expectation, content, inspected)
        target = self._resolve(expected.storage_path)
        sidecar = self._resolve(f"{expected.storage_path}.asset.json")
        self._create_parent(target.parent)
        with self._exclusive_lock(target):
            if target.exists() or sidecar.exists():
                return self._reuse_or_complete(
                    target=target,
                    sidecar=sidecar,
                    expectation=expectation,
                    content=content,
                )
            self._atomic_write(target, content)
            self._atomic_write(sidecar, serialize_audio_design_asset(expected))
        return expected

    def _recover_sync(
        self,
        expectation: AudioAssetExpectation,
    ) -> StoredAudioDesignAsset | None:
        self._validate_kind(expectation)
        relative = audio_asset_relative_path(expectation)
        target = self._resolve(relative)
        sidecar = self._resolve(f"{relative}.asset.json")
        if not target.exists() and not sidecar.exists():
            return None
        if sidecar.exists() and not target.exists():
            raise AudioDesignStoreConflictError("audio-design sidecar exists without WAV")
        content = self._read_bounded(target)
        expected = self._asset(
            expectation,
            content,
            self._validate(content, expectation),
        )
        with self._exclusive_lock(target):
            if sidecar.exists():
                self._compare_assets(self._read_sidecar(sidecar), expected)
            else:
                self._atomic_write(sidecar, serialize_audio_design_asset(expected))
        return expected

    def _resolve_sync(
        self,
        expectation: AudioAssetExpectation,
    ) -> StoredAudioDesignAsset:
        self._validate_kind(expectation)
        relative = audio_asset_relative_path(expectation)
        target = self._resolve(relative, require_exists=True)
        sidecar = self._resolve(f"{relative}.asset.json")
        content = self._read_bounded(target)
        expected = self._asset(
            expectation,
            content,
            self._validate(content, expectation),
        )
        if sidecar.exists():
            self._compare_assets(self._read_sidecar(sidecar), expected)
        return expected

    def _read_asset_sync(self, asset: StoredAudioDesignAsset) -> bytes:
        if asset.kind is not self._kind:
            raise AudioDesignStorePathError("audio asset belongs to another store")
        target = self._resolve(asset.storage_path, require_exists=True)
        content = self._read_bounded(target)
        if len(content) != asset.size_bytes:
            raise AudioDesignStoreIntegrityError("audio-design asset size differs")
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise AudioDesignStoreIntegrityError("audio-design asset checksum differs")
        inspected = self._validator.validate(
            content,
            expected=AudioWavExpectations(
                sample_rate_hz=asset.audio.sample_rate_hz,
                channel_count=asset.audio.channel_count,
                sample_width_bytes=asset.audio.sample_width_bytes,
                frame_count=asset.audio.frame_count,
                duration_ms=asset.audio.duration_ms,
            ),
        )
        if inspected != asset.audio:
            raise AudioDesignStoreIntegrityError("audio-design WAV metadata differs")
        return content

    def _reuse_or_complete(
        self,
        *,
        target: Path,
        sidecar: Path,
        expectation: AudioAssetExpectation,
        content: bytes,
    ) -> StoredAudioDesignAsset:
        if sidecar.exists() and not target.exists():
            raise AudioDesignStoreConflictError("audio-design sidecar exists without WAV")
        if not target.exists():
            raise AudioDesignStoreConflictError("audio-design WAV disappeared")
        existing = self._read_bounded(target)
        if existing != content:
            raise AudioDesignStoreConflictError("audio-design path contains incompatible content")
        expected = self._asset(
            expectation,
            existing,
            self._validate(existing, expectation),
        )
        if sidecar.exists():
            self._compare_assets(self._read_sidecar(sidecar), expected)
        else:
            self._atomic_write(sidecar, serialize_audio_design_asset(expected))
        return expected

    @staticmethod
    def _compare_assets(
        existing: StoredAudioDesignAsset,
        expected: StoredAudioDesignAsset,
    ) -> None:
        if existing != expected:
            raise AudioDesignStoreIntegrityError("audio-design sidecar differs from WAV")

    def _read_sidecar(self, path: Path) -> StoredAudioDesignAsset:
        try:
            self._confinement.reject_unsafe_file(path)
            with path.open("rb") as stream:
                content = stream.read(128_001)
            if len(content) > 128_000:
                raise AudioDesignStoreIntegrityError("audio-design sidecar exceeds safe size")
            return deserialize_audio_design_asset(content)
        except AudioDesignStoreIntegrityError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise AudioDesignStoreIntegrityError("audio-design sidecar is invalid") from exc

    def _validate(
        self,
        content: bytes,
        expectation: AudioAssetExpectation,
    ) -> AudioPcmMetadata:
        try:
            return self._validator.validate(
                content,
                expected=AudioWavExpectations(
                    sample_rate_hz=expectation.audio.sample_rate_hz,
                    channel_count=expectation.audio.channel_count,
                    sample_width_bytes=expectation.audio.sample_width_bytes,
                    frame_count=expectation.audio.frame_count,
                    duration_ms=expectation.audio.duration_ms,
                ),
            )
        except AudioDesignWavError as exc:
            raise AudioDesignStoreIntegrityError("audio-design WAV failed validation") from exc

    @staticmethod
    def _asset(
        expectation: AudioAssetExpectation,
        content: bytes,
        inspected: AudioPcmMetadata,
    ) -> StoredAudioDesignAsset:
        kind_token = "music" if expectation.kind is AudioAssetKind.MUSIC else "sfx"
        return StoredAudioDesignAsset(
            asset_id=(
                f"audio-{kind_token}-{expectation.requirement_id.split('-', 1)[1]}-"
                f"{expectation.request_fingerprint[:16]}"
            ),
            job_id=expectation.job_id,
            kind=expectation.kind,
            requirement_id=expectation.requirement_id,
            request_fingerprint=expectation.request_fingerprint,
            storage_path=audio_asset_relative_path(expectation),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            audio=inspected,
            provider_id=expectation.provider_id,
        )

    def _validate_kind(self, expectation: AudioAssetExpectation) -> None:
        if expectation.kind is not self._kind:
            raise AudioDesignStorePathError("audio expectation belongs to another store")

    def _resolve(self, relative: str, *, require_exists: bool = False) -> Path:
        try:
            return self._confinement.resolve(relative, require_exists=require_exists)
        except BinaryAssetNotFoundError as exc:
            raise AudioDesignStoreNotFoundError("audio-design asset is missing") from exc
        except (BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise AudioDesignStorePathError("audio-design path is unsafe") from exc

    def _create_parent(self, parent: Path) -> None:
        try:
            parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(parent)
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise AudioDesignStorePathError("audio-design directory is unsafe") from exc

    def _read_bounded(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise AudioDesignStoreIntegrityError("audio-design asset exceeds safe size")
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except AudioDesignStoreIntegrityError:
            raise
        except FileNotFoundError as exc:
            raise AudioDesignStoreNotFoundError("audio-design asset is missing") from exc
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise AudioDesignStorePathError("audio-design asset could not be read") from exc
        if len(content) > self._maximum:
            raise AudioDesignStoreIntegrityError("audio-design asset exceeds safe size")
        return content

    @contextmanager
    def _exclusive_lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AudioDesignStoreConflictError("audio-design asset is locked") from exc
        except OSError as exc:
            raise AudioDesignStorePathError("audio-design lock failed") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise AudioDesignStorePathError("audio-design lock release failed") from exc

    def _atomic_write(self, target: Path, content: bytes) -> None:
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".audio-design-",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._confinement.reject_unsafe_components(target)
            if target.exists() or target.is_symlink():
                raise AudioDesignStoreConflictError("audio-design target appeared concurrently")
            os.replace(temporary, target)
            temporary = None
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        except AudioDesignStoreConflictError:
            raise
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise AudioDesignStorePathError("audio-design asset could not be persisted") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def audio_asset_relative_path(expectation: AudioAssetExpectation) -> str:
    token = "music" if expectation.kind is AudioAssetKind.MUSIC else "sound-effects"
    return (
        f"production/{expectation.job_id}/assets/{token}/"
        f"{expectation.requirement_id}-{expectation.request_fingerprint[:16]}.wav"
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
