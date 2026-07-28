"""Atomic compare-and-swap storage for audio-design manifests."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.src.production.audio_design.exceptions import (
    AudioDesignManifestConflictError,
    AudioDesignManifestCorruptError,
)
from backend.src.production.audio_design.models import (
    AudioDesignAssetStatus,
    AudioDesignManifest,
    AudioDesignManifestStatus,
)
from backend.src.production.audio_design.ports import AudioDesignStageContext
from backend.src.production.audio_design.serialization import (
    deserialize_audio_design_manifest,
    serialize_audio_design_manifest,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.path_rules import validate_relative_path


class InMemoryAudioDesignManifestStore:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}
        self.checkpoint_count = 0

    async def read_existing(
        self,
        *,
        context: AudioDesignStageContext,
    ) -> AudioDesignManifest | None:
        content = self.contents.get(audio_design_manifest_relative_path(context))
        return deserialize_audio_design_manifest(content) if content is not None else None

    async def create(
        self,
        *,
        context: AudioDesignStageContext,
        manifest: AudioDesignManifest,
    ) -> None:
        path = audio_design_manifest_relative_path(context)
        if path in self.contents:
            raise AudioDesignManifestConflictError("audio-design manifest exists")
        self.contents[path] = serialize_audio_design_manifest(_validated(manifest))
        self.checkpoint_count += 1

    async def checkpoint(
        self,
        *,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None:
        _validate_transition(previous, current)
        path = audio_design_manifest_relative_path(context)
        if self.contents.get(path) != serialize_audio_design_manifest(previous):
            raise AudioDesignManifestConflictError("audio-design CAS conflict")
        self.contents[path] = serialize_audio_design_manifest(current)
        self.checkpoint_count += 1

    async def finalize(
        self,
        *,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None:
        if current.status is not AudioDesignManifestStatus.COMPLETE:
            raise AudioDesignManifestConflictError("final manifest must be complete")
        await self.checkpoint(context=context, previous=previous, current=current)


class LocalAudioDesignManifestStore:
    def __init__(self, workspace_root: Path, *, max_manifest_bytes: int) -> None:
        if not 1_024 <= max_manifest_bytes <= 16_000_000:
            raise ValueError("audio-design manifest limit is outside safe bounds")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_manifest_bytes

    async def read_existing(
        self,
        *,
        context: AudioDesignStageContext,
    ) -> AudioDesignManifest | None:
        return await asyncio.to_thread(self._read_existing_sync, context)

    async def create(
        self,
        *,
        context: AudioDesignStageContext,
        manifest: AudioDesignManifest,
    ) -> None:
        await asyncio.to_thread(self._create_sync, context, manifest)

    async def checkpoint(
        self,
        *,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None:
        await asyncio.to_thread(self._checkpoint_sync, context, previous, current)

    async def finalize(
        self,
        *,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None:
        if current.status is not AudioDesignManifestStatus.COMPLETE:
            raise AudioDesignManifestConflictError("final manifest must be complete")
        await self.checkpoint(context=context, previous=previous, current=current)

    def _read_existing_sync(
        self,
        context: AudioDesignStageContext,
    ) -> AudioDesignManifest | None:
        target = self._target(context)
        if not target.exists():
            return None
        return self._read(target)

    def _create_sync(
        self,
        context: AudioDesignStageContext,
        manifest: AudioDesignManifest,
    ) -> None:
        target = self._target(context)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._confinement.reject_unsafe_components(target)
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise AudioDesignManifestConflictError(
                "audio-design manifest directory is unsafe"
            ) from exc
        with self._exclusive_lock(target):
            if target.exists() or target.is_symlink():
                raise AudioDesignManifestConflictError("audio-design manifest exists")
            self._replace(target, serialize_audio_design_manifest(_validated(manifest)))

    def _checkpoint_sync(
        self,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None:
        _validate_transition(previous, current)
        target = self._target(context)
        with self._exclusive_lock(target):
            if self._read(target) != previous:
                raise AudioDesignManifestConflictError("audio-design CAS conflict")
            self._replace(target, serialize_audio_design_manifest(current))

    def _target(self, context: AudioDesignStageContext) -> Path:
        try:
            return self._confinement.resolve(audio_design_manifest_relative_path(context))
        except Exception as exc:
            raise AudioDesignManifestCorruptError("audio-design manifest path is unsafe") from exc

    def _read(self, target: Path) -> AudioDesignManifest:
        try:
            return deserialize_audio_design_manifest(self._read_limited(target))
        except AudioDesignManifestCorruptError:
            raise
        except (UnicodeError, ValueError, TypeError) as exc:
            raise AudioDesignManifestCorruptError("audio-design manifest is invalid") from exc

    def _read_limited(self, target: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise AudioDesignManifestCorruptError("audio-design manifest exceeds limit")
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except AudioDesignManifestCorruptError:
            raise
        except (OSError, BinaryAssetLinkError, BinaryAssetPathError) as exc:
            raise AudioDesignManifestCorruptError(
                "audio-design manifest could not be read"
            ) from exc
        if len(content) > self._maximum:
            raise AudioDesignManifestCorruptError("audio-design manifest exceeds limit")
        return content

    def _replace(self, target: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise AudioDesignManifestConflictError("audio-design manifest exceeds limit")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".audio-design-manifest-",
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
            raise AudioDesignManifestConflictError("audio-design manifest replace failed") from exc
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
            raise AudioDesignManifestConflictError("audio-design manifest is locked") from exc
        except OSError as exc:
            raise AudioDesignManifestConflictError("audio-design manifest lock failed") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                raise AudioDesignManifestConflictError(
                    "audio-design manifest lock release failed"
                ) from exc


def audio_design_manifest_relative_path(context: AudioDesignStageContext) -> str:
    expected = (
        f"production/{context.job_id}/preparing_music/"
        f"attempt-{context.attempt_number}/audio-design-manifest.json"
    )
    normalized = validate_relative_path(
        f"{context.workspace_relative_path}/audio-design-manifest.json"
    )
    if normalized != expected or "\\" in normalized:
        raise AudioDesignManifestConflictError("audio-design manifest path is not contractual")
    return normalized


def _validated(manifest: AudioDesignManifest) -> AudioDesignManifest:
    try:
        return AudioDesignManifest.model_validate(manifest.model_dump(mode="python"))
    except (TypeError, ValueError) as exc:
        raise AudioDesignManifestConflictError("audio-design manifest contract is invalid") from exc


def _validate_transition(
    previous: AudioDesignManifest,
    current: AudioDesignManifest,
) -> None:
    previous = _validated(previous)
    current = _validated(current)
    immutable = (
        "job_id",
        "stage",
        "attempt_number",
        "source_script_schema_version",
        "source_script_artifact_id",
        "production_script_fingerprint",
        "audio_design_plan_fingerprint",
        "configuration_fingerprint",
        "music_provider_id",
        "sound_effect_provider_id",
        "expected_music_requirement_id",
        "expected_sound_effect_requirement_ids",
        "created_at",
    )
    if any(getattr(previous, field) != getattr(current, field) for field in immutable):
        raise AudioDesignManifestConflictError("manifest identity changed")
    if len(previous.entries) != len(current.entries):
        raise AudioDesignManifestConflictError("manifest entry collection changed")
    allowed_status = {
        AudioDesignManifestStatus.PREPARED: {
            AudioDesignManifestStatus.GENERATING,
            AudioDesignManifestStatus.COMPLETE,
            AudioDesignManifestStatus.FAILED,
        },
        AudioDesignManifestStatus.GENERATING: {
            AudioDesignManifestStatus.GENERATING,
            AudioDesignManifestStatus.COMPLETE,
            AudioDesignManifestStatus.FAILED,
        },
        AudioDesignManifestStatus.FAILED: {
            AudioDesignManifestStatus.GENERATING,
            AudioDesignManifestStatus.FAILED,
        },
        AudioDesignManifestStatus.COMPLETE: {
            AudioDesignManifestStatus.COMPLETE,
            AudioDesignManifestStatus.FAILED,
        },
    }
    if current.status not in allowed_status[previous.status]:
        raise AudioDesignManifestConflictError("manifest status transition is illegal")
    entry_allowed = {
        AudioDesignAssetStatus.PENDING: {
            AudioDesignAssetStatus.PENDING,
            AudioDesignAssetStatus.GENERATING,
        },
        AudioDesignAssetStatus.GENERATING: {
            AudioDesignAssetStatus.GENERATING,
            AudioDesignAssetStatus.STORED,
            AudioDesignAssetStatus.FAILED,
        },
        AudioDesignAssetStatus.FAILED: {
            AudioDesignAssetStatus.FAILED,
            AudioDesignAssetStatus.GENERATING,
        },
        AudioDesignAssetStatus.STORED: {
            AudioDesignAssetStatus.STORED,
            AudioDesignAssetStatus.FAILED,
        },
    }
    for before, after in zip(previous.entries, current.entries, strict=True):
        if (
            before.requirement_id != after.requirement_id
            or before.request_fingerprint != after.request_fingerprint
            or before.expected_audio != after.expected_audio
            or before.kind is not after.kind
        ):
            raise AudioDesignManifestConflictError("manifest entry identity changed")
        if after.status not in entry_allowed[before.status]:
            raise AudioDesignManifestConflictError("entry status transition is illegal")
        if after.generation_attempt_count < before.generation_attempt_count:
            raise AudioDesignManifestConflictError("generation attempt count regressed")
        if (
            before.status is AudioDesignAssetStatus.STORED
            and after.status is AudioDesignAssetStatus.STORED
            and after != before
        ):
            raise AudioDesignManifestConflictError("stored manifest entry is immutable")


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
