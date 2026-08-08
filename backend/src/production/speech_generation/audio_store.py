"""Speech-owned write-once durable PCM WAV storage."""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetNotFoundError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.speech_generation.exceptions import (
    SpeechAudioChecksumError,
    SpeechAudioConflictError,
    SpeechAudioIntegrityError,
    SpeechAudioLinkError,
    SpeechAudioNotFoundError,
    SpeechAudioPathError,
    SpeechAudioStoreError,
)
from backend.src.production.speech_generation.models import (
    ReadSpeechBinaryAsset,
    SpeechAudioWriteRequest,
    SpeechBinaryAsset,
    SpeechSegmentAudioMetadata,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_asset,
    serialize_speech_asset,
)
from backend.src.production.speech_generation.wav import (
    SpeechWavValidator,
    WavExpectations,
)


class FilesystemSpeechAudioStore:
    """Store verified WAV and strict sidecar under the contractual speech path."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        validator: SpeechWavValidator,
        max_audio_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._validator = validator
        self._maximum = max_audio_bytes
        self._clock = clock

    async def write(
        self,
        *,
        request: SpeechAudioWriteRequest,
        content: bytes,
    ) -> SpeechBinaryAsset:
        return await asyncio.to_thread(self._write_sync, request, content)

    async def recover(
        self,
        *,
        request: SpeechAudioWriteRequest,
    ) -> SpeechBinaryAsset | None:
        return await asyncio.to_thread(self._recover_sync, request)

    async def resolve(
        self,
        *,
        job_id: UUID,
        segment_id: str,
    ) -> ReadSpeechBinaryAsset:
        relative = speech_audio_relative_path(job_id=job_id, segment_id=segment_id)
        sidecar = self._resolve(f"{relative}.asset.json", require_exists=True)
        asset = self._read_sidecar(sidecar)
        if asset.job_id != job_id or asset.segment_id != segment_id:
            raise SpeechAudioIntegrityError("speech asset identity differs from sidecar")
        return await self.read(asset=asset)

    async def read(self, *, asset: SpeechBinaryAsset) -> ReadSpeechBinaryAsset:
        content = await asyncio.to_thread(self._read_content_sync, asset)
        return ReadSpeechBinaryAsset(asset=asset, content=content)

    def _write_sync(
        self,
        request: SpeechAudioWriteRequest,
        content: bytes,
    ) -> SpeechBinaryAsset:
        inspected = self._validate_content(content, request)
        asset = self._asset(request, content, inspected.duration_ms, inspected.frame_count)
        target = self._resolve(asset.storage_path)
        sidecar = self._resolve(f"{asset.storage_path}.asset.json")
        self._create_parent(target.parent)
        with self._exclusive_lock(target):
            if target.exists() or sidecar.exists():
                return self._reuse_or_complete(
                    target=target,
                    sidecar=sidecar,
                    expected=asset,
                    request=request,
                )
            self._atomic_write(target, content)
            self._atomic_write(sidecar, serialize_speech_asset(asset))
            return asset

    def _recover_sync(
        self,
        request: SpeechAudioWriteRequest,
    ) -> SpeechBinaryAsset | None:
        relative = speech_audio_relative_path(
            job_id=request.job_id,
            segment_id=request.segment.segment_id,
        )
        target = self._resolve(relative)
        sidecar = self._resolve(f"{relative}.asset.json")
        if not target.exists() and not sidecar.exists():
            return None
        if sidecar.exists() and not target.exists():
            raise SpeechAudioConflictError("speech sidecar exists without audio")
        content = self._read_bounded_file(target)
        inspected = self._validate_content(content, request)
        expected = self._asset(
            request,
            content,
            inspected.duration_ms,
            inspected.frame_count,
        )
        with self._exclusive_lock(target):
            if sidecar.exists():
                existing = self._read_sidecar(sidecar)
                self._compare_assets(existing, expected)
                self._read_content_sync(existing)
                return existing
            self._atomic_write(sidecar, serialize_speech_asset(expected))
            return expected

    def _reuse_or_complete(
        self,
        *,
        target: Path,
        sidecar: Path,
        expected: SpeechBinaryAsset,
        request: SpeechAudioWriteRequest,
    ) -> SpeechBinaryAsset:
        if sidecar.exists() and not target.exists():
            raise SpeechAudioConflictError("speech sidecar exists without audio")
        if not target.exists():
            raise SpeechAudioConflictError("speech audio disappeared concurrently")
        content = self._read_bounded_file(target)
        self._validate_content(content, request)
        if hashlib.sha256(content).hexdigest() != expected.sha256:
            raise SpeechAudioConflictError("existing speech audio is incompatible")
        if sidecar.exists():
            existing = self._read_sidecar(sidecar)
            self._compare_assets(existing, expected)
            return existing
        self._atomic_write(sidecar, serialize_speech_asset(expected))
        return expected

    @staticmethod
    def _compare_assets(
        existing: SpeechBinaryAsset,
        expected: SpeechBinaryAsset,
    ) -> None:
        comparable = existing.model_copy(update={"created_at": expected.created_at})
        if comparable != expected:
            raise SpeechAudioConflictError("existing speech sidecar is incompatible")

    def _asset(
        self,
        request: SpeechAudioWriteRequest,
        content: bytes,
        duration_ms: int,
        frame_count: int,
    ) -> SpeechBinaryAsset:
        created_at = self._clock()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("speech audio clock must be timezone-aware")
        segment = request.segment
        return SpeechBinaryAsset(
            asset_id=request.asset_id,
            segment_id=segment.segment_id,
            job_id=request.job_id,
            scene_id=segment.scene_id,
            shot_id=segment.shot_id,
            sequence_index=segment.sequence_index,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            duration_ms=duration_ms,
            sample_rate_hz=request.expected.sample_rate_hz,
            channel_count=request.expected.channel_count,
            sample_width_bytes=request.expected.sample_width_bytes,
            frame_count=frame_count,
            created_at=created_at,
            storage_path=speech_audio_relative_path(
                job_id=request.job_id,
                segment_id=segment.segment_id,
            ),
            metadata=request.metadata,
        )

    def _validate_content(
        self,
        content: bytes,
        request: SpeechAudioWriteRequest,
    ) -> SpeechSegmentAudioMetadata:
        expected = request.expected
        return self._validator.validate(
            content,
            expected=WavExpectations(
                sample_rate_hz=expected.sample_rate_hz,
                channel_count=expected.channel_count,
                sample_width_bytes=expected.sample_width_bytes,
                frame_count=None if request.flexible_duration else expected.frame_count,
                duration_ms=None if request.flexible_duration else expected.duration_ms,
            ),
        )

    def _read_content_sync(self, asset: SpeechBinaryAsset) -> bytes:
        target = self._resolve(asset.storage_path, require_exists=True)
        content = self._read_bounded_file(target)
        if len(content) != asset.size_bytes:
            raise SpeechAudioIntegrityError("speech audio size differs from sidecar")
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise SpeechAudioChecksumError("speech audio checksum differs from sidecar")
        inspected = self._validator.validate(
            content,
            expected=WavExpectations(
                sample_rate_hz=asset.sample_rate_hz,
                channel_count=asset.channel_count,
                sample_width_bytes=asset.sample_width_bytes,
                frame_count=asset.frame_count,
                duration_ms=asset.duration_ms,
            ),
        )
        if inspected.duration_ms != asset.duration_ms:
            raise SpeechAudioIntegrityError("speech WAV metadata differs from sidecar")
        return content

    def _read_bounded_file(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise SpeechAudioIntegrityError("speech audio exceeds configured limit")
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
                status = os.fstat(stream.fileno())
        except SpeechAudioStoreError:
            raise
        except FileNotFoundError as exc:
            raise SpeechAudioNotFoundError("speech audio is missing") from exc
        except BinaryAssetLinkError as exc:
            raise SpeechAudioLinkError("speech audio contains an unsafe link") from exc
        except (OSError, BinaryAssetPathError) as exc:
            raise SpeechAudioPathError("speech audio could not be read safely") from exc
        if len(content) > self._maximum:
            raise SpeechAudioIntegrityError("speech audio exceeds configured limit")
        if status.st_nlink != 1:
            raise SpeechAudioPathError("speech audio hard links are not allowed")
        return content

    def _read_sidecar(self, path: Path) -> SpeechBinaryAsset:
        try:
            self._confinement.reject_unsafe_file(path)
            with path.open("rb") as stream:
                content = stream.read(128_001)
            if len(content) > 128_000:
                raise SpeechAudioIntegrityError("speech sidecar exceeds safe limit")
            return deserialize_speech_asset(content)
        except SpeechAudioStoreError:
            raise
        except BinaryAssetLinkError as exc:
            raise SpeechAudioLinkError("speech sidecar contains an unsafe link") from exc
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise SpeechAudioIntegrityError("speech sidecar is invalid") from exc

    def _resolve(self, relative: str, *, require_exists: bool = False) -> Path:
        try:
            return self._confinement.resolve(relative, require_exists=require_exists)
        except BinaryAssetNotFoundError as exc:
            raise SpeechAudioNotFoundError("speech audio is missing") from exc
        except BinaryAssetLinkError as exc:
            raise SpeechAudioLinkError("speech audio path contains an unsafe link") from exc
        except BinaryAssetPathError as exc:
            raise SpeechAudioPathError("speech audio path is unsafe") from exc

    def _create_parent(self, parent: Path) -> None:
        try:
            parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(parent)
        except BinaryAssetLinkError as exc:
            raise SpeechAudioLinkError("speech audio directory contains a link") from exc
        except (OSError, BinaryAssetPathError) as exc:
            raise SpeechAudioPathError("speech audio directory is unsafe") from exc

    @contextmanager
    def _exclusive_lock(self, target: Path) -> Iterator[None]:
        lock = target.with_name(f".{target.name}.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise SpeechAudioConflictError("speech audio is already being written") from exc
        except OSError as exc:
            raise SpeechAudioPathError("speech audio lock could not be created") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise SpeechAudioPathError("speech audio lock could not be released") from exc

    def _atomic_write(self, target: Path, content: bytes) -> None:
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".speech-",
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
                raise SpeechAudioConflictError("speech audio target appeared concurrently")
            os.replace(temporary, target)
            temporary = None
            _fsync_directory(target.parent)
            self._confinement.reject_unsafe_file(target)
        except SpeechAudioConflictError:
            raise
        except BinaryAssetLinkError as exc:
            raise SpeechAudioLinkError("speech audio target contains a link") from exc
        except (OSError, BinaryAssetPathError) as exc:
            raise SpeechAudioPathError("speech audio could not be written safely") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def speech_audio_relative_path(*, job_id: UUID, segment_id: str) -> str:
    if not segment_id.startswith("segment-") or len(segment_id) != 40:
        raise SpeechAudioPathError("speech segment identity is invalid")
    return f"production/{job_id}/assets/speech/speech-{segment_id}.wav"


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
