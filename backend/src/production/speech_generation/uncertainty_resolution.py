"""Durable, provider-neutral resolution of uncertain speech submissions.

Resolution is deliberately separate from remote speech records and from
submission.  In particular, a resolution can make a later retry eligible,
but it never performs that retry itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from contextlib import suppress
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.exceptions import (
    SpeechUncertaintyResolutionError,
)
from backend.src.production.speech_generation.models import (
    SpeechSegmentStatus,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobStatus,
)
from backend.src.production.speech_generation.remote_serialization import (
    deserialize_remote_speech_job,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
)


class SpeechSubmissionResolutionStatus(StrEnum):
    CONFIRMED_COMPLETED = "confirmed_completed"
    CONFIRMED_NOT_SUBMITTED = "confirmed_not_submitted"
    CONFIRMED_FAILED = "confirmed_failed"
    UNRESOLVED = "unresolved"


class SpeechSubmissionResolutionProvenance(StrEnum):
    OPERATOR_ASSERTED = "operator_asserted"
    PROVIDER_RECONCILIATION = "provider_reconciliation"
    LOCAL_ARTIFACT = "local_artifact"


SUPPORTED_SPEECH_RESOLUTION_VERSIONS = frozenset({"1.0.0"})


class SpeechSubmissionResolution(ContractModel):
    """Immutable operator/provider evidence about one uncertain request."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    attempt_number: int = Field(ge=1)
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_status: Literal["uncertain"] = "uncertain"
    resolution: SpeechSubmissionResolutionStatus
    provenance: SpeechSubmissionResolutionProvenance
    resolved_at: datetime
    operator_id: str = Field(min_length=1, max_length=100)
    evidence_reference: str | None = Field(default=None, min_length=1, max_length=300)
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=300)
    remote_generation_id: str | None = Field(default=None, min_length=1, max_length=300)
    recovered_audio_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    acknowledge_new_submission: bool = False
    historical_cost_preserved: bool = True
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def create(cls, **values: Any) -> SpeechSubmissionResolution:
        """Build and validate a resolution with its canonical fingerprint."""
        candidate = cls.model_construct(fingerprint="0" * 64, **values)
        return cls.model_validate(
            candidate.model_copy(
                update={"fingerprint": speech_submission_resolution_fingerprint(candidate)}
            ).model_dump(mode="python")
        )

    @field_validator("resolved_at")
    @classmethod
    def aware_resolution_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech resolution time must be timezone-aware")
        return value

    @field_validator(
        "operator_id",
        "evidence_reference",
        "provider_request_id",
        "remote_generation_id",
    )
    @classmethod
    def safe_identity(cls, value: str | None) -> str | None:
        if value is not None and any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:/"
            for character in value
        ):
            raise ValueError("speech resolution identity contains unsafe characters")
        return value

    @model_validator(mode="after")
    def validate_resolution(self) -> SpeechSubmissionResolution:
        if self.resolution is SpeechSubmissionResolutionStatus.CONFIRMED_NOT_SUBMITTED:
            if not self.acknowledge_new_submission:
                raise ValueError("confirmed-not-submitted requires explicit retry acknowledgement")
            if self.provider_request_id or self.remote_generation_id:
                raise ValueError("not-submitted resolution cannot contain remote identity")
        elif self.acknowledge_new_submission:
            raise ValueError("retry acknowledgement is valid only for not-submitted resolution")

        if self.resolution in {
            SpeechSubmissionResolutionStatus.CONFIRMED_COMPLETED,
            SpeechSubmissionResolutionStatus.CONFIRMED_FAILED,
        } and not (
            self.provider_request_id
            or self.remote_generation_id
            or self.recovered_audio_sha256
            or self.evidence_reference
        ):
            raise ValueError("confirmed provider outcome requires bounded evidence")
        if self.resolution is SpeechSubmissionResolutionStatus.UNRESOLVED and any(
            value is not None
            for value in (
                self.provider_request_id,
                self.remote_generation_id,
                self.recovered_audio_sha256,
            )
        ):
            raise ValueError("unresolved resolution cannot claim outcome identity")
        expected = speech_submission_resolution_fingerprint(self)
        if self.fingerprint != expected:
            raise ValueError("speech resolution fingerprint differs")
        return self

    @property
    def fresh_submission_eligible(self) -> bool:
        return (
            self.resolution is SpeechSubmissionResolutionStatus.CONFIRMED_NOT_SUBMITTED
            and self.acknowledge_new_submission
        )


def speech_submission_resolution_fingerprint(
    resolution: SpeechSubmissionResolution,
) -> str:
    payload = resolution.model_dump(mode="json", exclude={"fingerprint"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def serialize_speech_submission_resolution(
    resolution: SpeechSubmissionResolution,
) -> bytes:
    return (
        json.dumps(
            resolution.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def deserialize_speech_submission_resolution(
    content: bytes,
) -> SpeechSubmissionResolution:
    payload = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )
    return SpeechSubmissionResolution.model_validate(payload)


class SpeechSubmissionResolutionStore:
    """Write-once sidecars with deterministic identity and conflict checks."""

    def __init__(self, workspace_root: Path, *, max_bytes: int = 100_000) -> None:
        if not 4_096 <= max_bytes <= 1_000_000:
            raise ValueError("speech resolution maximum size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_bytes

    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        segment_id: str,
    ) -> SpeechSubmissionResolution | None:
        return await asyncio.to_thread(
            self._read_sync,
            job_id,
            attempt_number,
            segment_id,
        )

    async def create_if_idempotent(
        self,
        resolution: SpeechSubmissionResolution,
    ) -> bool:
        return await asyncio.to_thread(self._create_sync, resolution)

    def _path(self, job_id: UUID, attempt_number: int, segment_id: str) -> Path:
        if attempt_number < 1 or not segment_id.startswith("segment-") or len(segment_id) != 40:
            raise SpeechUncertaintyResolutionError("speech resolution identity is invalid")
        relative = (
            f"production/{job_id}/generating_narration/attempt-{attempt_number}/"
            f"speech-resolutions/{segment_id}.json"
        )
        try:
            return self._confinement.resolve(relative)
        except Exception as exc:
            raise SpeechUncertaintyResolutionError("speech resolution path is unsafe") from exc

    def _read_sync(
        self,
        job_id: UUID,
        attempt_number: int,
        segment_id: str,
    ) -> SpeechSubmissionResolution | None:
        path = self._path(job_id, attempt_number, segment_id)
        if not path.exists():
            return None
        try:
            self._confinement.reject_unsafe_file(path)
            content = path.read_bytes()
            if not content or len(content) > self._maximum:
                raise ValueError("speech resolution size is invalid")
            result = deserialize_speech_submission_resolution(content)
            if (
                result.job_id != job_id
                or result.attempt_number != attempt_number
                or result.segment_id != segment_id
            ):
                raise ValueError("speech resolution path identity differs")
            return result
        except Exception as exc:
            if isinstance(exc, SpeechUncertaintyResolutionError):
                raise
            raise SpeechUncertaintyResolutionError("speech resolution sidecar is invalid") from exc

    def _create_sync(self, resolution: SpeechSubmissionResolution) -> bool:
        path = self._path(resolution.job_id, resolution.attempt_number, resolution.segment_id)
        content = serialize_speech_submission_resolution(resolution)
        if len(content) > self._maximum:
            raise SpeechUncertaintyResolutionError("speech resolution exceeds safe limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(path.parent)
        lock = path.with_name(f".sr-{resolution.segment_id[-12:]}.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        descriptor = -1
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            descriptor = -1
            if path.exists():
                existing = self._read_sync(
                    resolution.job_id,
                    resolution.attempt_number,
                    resolution.segment_id,
                )
                if existing == resolution:
                    return True
                raise SpeechUncertaintyResolutionError(
                    "conflicting speech resolution already exists"
                )
            temporary: Path | None = None
            try:
                descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".s-")
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = -1
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                temporary = None
                return True
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        except FileExistsError as exc:
            raise SpeechUncertaintyResolutionError(
                "speech resolution is being written concurrently"
            ) from exc
        except SpeechUncertaintyResolutionError:
            raise
        except OSError as exc:
            raise SpeechUncertaintyResolutionError("speech resolution could not be persisted") from exc
        finally:
            with suppress(OSError):
                lock.unlink()


class SpeechUncertaintyResolver:
    """Validate an uncertain record and persist only an explicit resolution."""

    def __init__(self, workspace_root: Path) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._store = SpeechSubmissionResolutionStore(workspace_root)

    async def resolve(
        self,
        resolution: SpeechSubmissionResolution,
    ) -> bool:
        record_path = self._confinement.resolve(
            f"production/{resolution.job_id}/generating_narration/attempt-"
            f"{resolution.attempt_number}/remote-speech-jobs/{resolution.segment_id}.json",
            require_exists=True,
        )
        manifest_path = self._confinement.resolve(
            f"production/{resolution.job_id}/generating_narration/attempt-"
            f"{resolution.attempt_number}/speech-generation-manifest.json",
            require_exists=True,
        )
        try:
            record = deserialize_remote_speech_job(record_path.read_bytes())
            manifest = deserialize_speech_manifest(manifest_path.read_bytes())
        except Exception as exc:
            raise SpeechUncertaintyResolutionError(
                "speech uncertainty source artifacts are invalid"
            ) from exc
        if record.status is not RemoteSpeechJobStatus.UNCERTAIN:
            raise SpeechUncertaintyResolutionError("source speech record is not uncertain")
        if record.request_fingerprint != resolution.request_fingerprint:
            raise SpeechUncertaintyResolutionError("speech request fingerprint drifted")
        entry = next((item for item in manifest.entries if item.segment_id == resolution.segment_id), None)
        if entry is None or entry.source_scene_id != resolution.scene_id:
            raise SpeechUncertaintyResolutionError("speech scene or segment identity drifted")
        if entry.status is not SpeechSegmentStatus.UNCERTAIN:
            raise SpeechUncertaintyResolutionError("speech manifest entry is not uncertain")
        existing = await self._store.read(
            job_id=resolution.job_id,
            attempt_number=resolution.attempt_number,
            segment_id=resolution.segment_id,
        )
        if existing is not None and existing != resolution:
            raise SpeechUncertaintyResolutionError("conflicting speech resolution already exists")
        return await self._store.create_if_idempotent(resolution)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


__all__ = [
    "SpeechSubmissionResolution",
    "SpeechSubmissionResolutionProvenance",
    "SpeechSubmissionResolutionStatus",
    "SpeechSubmissionResolutionStore",
    "SpeechUncertaintyResolver",
    "deserialize_speech_submission_resolution",
    "serialize_speech_submission_resolution",
    "speech_submission_resolution_fingerprint",
]
