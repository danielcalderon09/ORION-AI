"""One-shot authorization for replacing an unresolved remote speech request."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.exceptions import (
    SpeechUncertaintyResolutionError,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
)
from backend.src.production.speech_generation.remote_serialization import (
    deserialize_remote_speech_job,
    serialize_remote_speech_job,
)
from backend.src.production.speech_generation.uncertainty_resolution import (
    SpeechSubmissionResolution,
    SpeechSubmissionResolutionStatus,
    deserialize_speech_submission_resolution,
)


@dataclass(frozen=True, slots=True)
class _SpeechReplacementSource:
    record: RemoteSpeechJobRecord
    resolution: SpeechSubmissionResolution
    durable_record_sha256: str
    canonical_record_sha256: str

    def matches_record_hash(self, expected_sha256: str) -> bool:
        return expected_sha256 in {
            self.durable_record_sha256,
            self.canonical_record_sha256,
        }


class SpeechUnresolvedReplacementAuthorization(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    source_attempt_number: int = Field(ge=1)
    target_attempt_number: int = Field(ge=2)
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    original_request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_uncertain_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    unresolved_resolution_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    maximum_additional_provider_requests: Literal[1]
    maximum_additional_estimated_cost_usd: Decimal = Field(gt=0)
    currency: Literal["USD"] = "USD"
    authorized_at: datetime
    operator_id: str = Field(min_length=1, max_length=100)
    acknowledge_duplicate_charge_risk: bool
    historical_uncertain_record_preserved: Literal[True] = True
    historical_cost_exposure_preserved: Literal[True] = True
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def create(cls, **values: Any) -> SpeechUnresolvedReplacementAuthorization:
        candidate = cls.model_construct(fingerprint="0" * 64, **values)
        return cls.model_validate(
            candidate.model_copy(
                update={"fingerprint": unresolved_replacement_authorization_fingerprint(candidate)}
            ).model_dump(mode="python")
        )

    @field_validator("maximum_additional_estimated_cost_usd", mode="before")
    @classmethod
    def decimal_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("replacement authorization money must not use float")
        return value

    @field_validator("authorized_at")
    @classmethod
    def aware_authorized_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replacement authorization time must be timezone-aware")
        return value

    @field_validator("operator_id")
    @classmethod
    def safe_operator(cls, value: str) -> str:
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:" for character in value):
            raise ValueError("replacement authorization operator identity is unsafe")
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> SpeechUnresolvedReplacementAuthorization:
        if self.target_attempt_number != self.source_attempt_number + 1:
            raise ValueError(
                "replacement authorization must target the next remote submission attempt"
            )
        if not self.acknowledge_duplicate_charge_risk:
            raise ValueError("duplicate-charge risk acknowledgement is required")
        if self.fingerprint != unresolved_replacement_authorization_fingerprint(self):
            raise ValueError("replacement authorization fingerprint differs")
        return self


class SpeechUnresolvedReplacementConsumption(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    target_attempt_number: int = Field(ge=2)
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    authorization_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    replacement_submission_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimated_cost_usd: Decimal = Field(gt=0)
    consumed_at: datetime
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def create(cls, **values: Any) -> SpeechUnresolvedReplacementConsumption:
        candidate = cls.model_construct(fingerprint="0" * 64, **values)
        return cls.model_validate(
            candidate.model_copy(
                update={"fingerprint": unresolved_replacement_consumption_fingerprint(candidate)}
            ).model_dump(mode="python")
        )

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def decimal_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("replacement consumption money must not use float")
        return value

    @field_validator("consumed_at")
    @classmethod
    def aware_consumed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replacement consumption time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_fingerprint(self) -> SpeechUnresolvedReplacementConsumption:
        if self.fingerprint != unresolved_replacement_consumption_fingerprint(self):
            raise ValueError("replacement consumption fingerprint differs")
        return self


class SpeechUnresolvedReplacementPermit(ContractModel):
    authorization: SpeechUnresolvedReplacementAuthorization
    replacement_submission_identity: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> SpeechUnresolvedReplacementPermit:
        if self.replacement_submission_identity != replacement_submission_identity(
            self.authorization
        ):
            raise ValueError("replacement submission identity differs")
        return self


def unresolved_replacement_authorization_fingerprint(
    value: SpeechUnresolvedReplacementAuthorization,
) -> str:
    return _fingerprint(value.model_dump(mode="json", exclude={"fingerprint"}))


def unresolved_replacement_consumption_fingerprint(
    value: SpeechUnresolvedReplacementConsumption,
) -> str:
    return _fingerprint(value.model_dump(mode="json", exclude={"fingerprint"}))


def replacement_submission_identity(
    authorization: SpeechUnresolvedReplacementAuthorization,
) -> str:
    return _fingerprint(
        {
            "schema_version": "1.0.0",
            "authorization_fingerprint": authorization.fingerprint,
            "job_id": str(authorization.job_id),
            "target_attempt_number": authorization.target_attempt_number,
            "segment_id": authorization.segment_id,
        }
    )


class SpeechUnresolvedReplacementStore:
    """Validate, persist, and consume one unresolved replacement authorization."""

    def __init__(self, workspace_root: Path, *, max_bytes: int = 100_000) -> None:
        if not 4_096 <= max_bytes <= 1_000_000:
            raise ValueError("replacement artifact maximum size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = max_bytes

    async def authorize(
        self,
        *,
        job_id: UUID,
        source_attempt_number: int,
        scene_id: str,
        segment_id: str,
        request_fingerprint: str,
        resolution_fingerprint: str,
        maximum_additional_provider_requests: int,
        maximum_additional_estimated_cost_usd: Decimal,
        authorized_at: datetime,
        operator_id: str,
        acknowledge_duplicate_charge_risk: bool,
    ) -> SpeechUnresolvedReplacementAuthorization:
        return await asyncio.to_thread(
            self._authorize_sync,
            job_id,
            source_attempt_number,
            scene_id,
            segment_id,
            request_fingerprint,
            resolution_fingerprint,
            maximum_additional_provider_requests,
            maximum_additional_estimated_cost_usd,
            authorized_at,
            operator_id,
            acknowledge_duplicate_charge_risk,
        )

    async def permit(
        self,
        *,
        job_id: UUID,
        target_attempt_number: int,
        segment_id: str,
        estimated_cost_usd: Decimal,
    ) -> SpeechUnresolvedReplacementPermit | None:
        return await asyncio.to_thread(
            self._permit_sync,
            job_id,
            target_attempt_number,
            segment_id,
            estimated_cost_usd,
        )

    async def consume(
        self,
        *,
        permit: SpeechUnresolvedReplacementPermit,
        estimated_cost_usd: Decimal,
        consumed_at: datetime,
    ) -> SpeechUnresolvedReplacementConsumption:
        return await asyncio.to_thread(
            self._consume_sync,
            permit,
            estimated_cost_usd,
            consumed_at,
        )

    async def uncertainty_is_covered(
        self,
        *,
        record: RemoteSpeechJobRecord,
        job_records: tuple[RemoteSpeechJobRecord, ...],
    ) -> bool:
        return await asyncio.to_thread(self._uncertainty_is_covered_sync, record, job_records)

    def _authorize_sync(
        self,
        job_id: UUID,
        source_attempt: int,
        scene_id: str,
        segment_id: str,
        request_fingerprint: str,
        resolution_fingerprint: str,
        maximum_requests: int,
        maximum_cost: Decimal,
        authorized_at: datetime,
        operator_id: str,
        acknowledge_risk: bool,
    ) -> SpeechUnresolvedReplacementAuthorization:
        source = self._source(job_id, source_attempt, segment_id)
        record = source.record
        resolution = source.resolution
        if record.status is not RemoteSpeechJobStatus.UNCERTAIN:
            raise SpeechUncertaintyResolutionError("replacement source is not uncertain")
        if record.request_fingerprint != request_fingerprint:
            raise SpeechUncertaintyResolutionError("replacement request fingerprint drifted")
        if resolution.scene_id != scene_id or resolution.request_fingerprint != request_fingerprint:
            raise SpeechUncertaintyResolutionError("replacement resolution identity drifted")
        if resolution.resolution is not SpeechSubmissionResolutionStatus.UNRESOLVED:
            raise SpeechUncertaintyResolutionError("replacement requires an unresolved resolution")
        if resolution.fingerprint != resolution_fingerprint:
            raise SpeechUncertaintyResolutionError("replacement resolution fingerprint drifted")
        target_attempt = source_attempt + 1
        existing_path = self._authorization_path_for(job_id, target_attempt, segment_id)
        if existing_path.exists():
            existing = SpeechUnresolvedReplacementAuthorization.model_validate(
                _deserialize(self._read(existing_path))
            )
            if (
                existing.scene_id == scene_id
                and existing.original_request_fingerprint == request_fingerprint
                and existing.unresolved_resolution_fingerprint == resolution_fingerprint
                and existing.maximum_additional_provider_requests == maximum_requests
                and existing.maximum_additional_estimated_cost_usd == maximum_cost
                and existing.operator_id == operator_id
                and existing.acknowledge_duplicate_charge_risk == acknowledge_risk
            ):
                return existing
            raise SpeechUncertaintyResolutionError(
                "conflicting replacement authorization already exists"
            )
        authorization = SpeechUnresolvedReplacementAuthorization.create(
            job_id=job_id,
            source_attempt_number=source_attempt,
            target_attempt_number=target_attempt,
            scene_id=scene_id,
            segment_id=segment_id,
            original_request_fingerprint=request_fingerprint,
            original_uncertain_record_sha256=source.canonical_record_sha256,
            unresolved_resolution_fingerprint=resolution_fingerprint,
            maximum_additional_provider_requests=maximum_requests,
            maximum_additional_estimated_cost_usd=maximum_cost,
            authorized_at=authorized_at,
            operator_id=operator_id,
            acknowledge_duplicate_charge_risk=acknowledge_risk,
        )
        self._write_once(self._authorization_path(authorization), _serialize(authorization))
        return authorization

    def _permit_sync(
        self,
        job_id: UUID,
        target_attempt: int,
        segment_id: str,
        estimated_cost: Decimal,
    ) -> SpeechUnresolvedReplacementPermit | None:
        path = self._authorization_path_for(job_id, target_attempt, segment_id)
        if not path.exists():
            return None
        authorization = SpeechUnresolvedReplacementAuthorization.model_validate(
            _deserialize(self._read(path))
        )
        if (
            authorization.job_id != job_id
            or authorization.target_attempt_number != target_attempt
            or authorization.source_attempt_number + 1 != target_attempt
            or authorization.segment_id != segment_id
        ):
            raise SpeechUncertaintyResolutionError(
                "replacement authorization lineage identity drifted"
            )
        if estimated_cost > authorization.maximum_additional_estimated_cost_usd:
            raise SpeechUncertaintyResolutionError("replacement estimated cost exceeds authorization")
        source = self._source(
            job_id, authorization.source_attempt_number, segment_id
        )
        record = source.record
        resolution = source.resolution
        if (
            record.job_id != authorization.job_id
            or record.attempt_number != authorization.source_attempt_number
            or record.segment_id != authorization.segment_id
            or record.status is not RemoteSpeechJobStatus.UNCERTAIN
            or record.request_fingerprint != authorization.original_request_fingerprint
            or not source.matches_record_hash(
                authorization.original_uncertain_record_sha256
            )
            or resolution.job_id != authorization.job_id
            or resolution.attempt_number != authorization.source_attempt_number
            or resolution.scene_id != authorization.scene_id
            or resolution.segment_id != authorization.segment_id
            or resolution.request_fingerprint
            != authorization.original_request_fingerprint
            or resolution.fingerprint != authorization.unresolved_resolution_fingerprint
            or resolution.resolution is not SpeechSubmissionResolutionStatus.UNRESOLVED
        ):
            raise SpeechUncertaintyResolutionError("replacement authorization source drifted")
        if self._consumption_path_for(job_id, target_attempt, segment_id).exists():
            raise SpeechUncertaintyResolutionError("replacement authorization is exhausted")
        return SpeechUnresolvedReplacementPermit(
            authorization=authorization,
            replacement_submission_identity=replacement_submission_identity(authorization),
        )

    def _consume_sync(
        self,
        permit: SpeechUnresolvedReplacementPermit,
        estimated_cost: Decimal,
        consumed_at: datetime,
    ) -> SpeechUnresolvedReplacementConsumption:
        authorization = permit.authorization
        current = self._permit_sync(
            authorization.job_id,
            authorization.target_attempt_number,
            authorization.segment_id,
            estimated_cost,
        )
        if current is None or current != permit:
            raise SpeechUncertaintyResolutionError("replacement permit differs from authorization")
        consumption = SpeechUnresolvedReplacementConsumption.create(
            job_id=authorization.job_id,
            target_attempt_number=authorization.target_attempt_number,
            segment_id=authorization.segment_id,
            authorization_fingerprint=authorization.fingerprint,
            replacement_submission_identity=permit.replacement_submission_identity,
            estimated_cost_usd=estimated_cost,
            consumed_at=consumed_at,
        )
        self._write_once(self._consumption_path(consumption), _serialize(consumption))
        return consumption

    def _uncertainty_is_covered_sync(
        self,
        record: RemoteSpeechJobRecord,
        job_records: tuple[RemoteSpeechJobRecord, ...],
    ) -> bool:
        path = self._authorization_path_for(
            record.job_id, record.attempt_number + 1, record.segment_id
        )
        consumption_path = self._consumption_path_for(
            record.job_id, record.attempt_number + 1, record.segment_id
        )
        if not path.exists() or not consumption_path.exists():
            return False
        authorization = SpeechUnresolvedReplacementAuthorization.model_validate(
            _deserialize(self._read(path))
        )
        consumption = SpeechUnresolvedReplacementConsumption.model_validate(
            _deserialize(self._read(consumption_path))
        )
        source = self._source(
            record.job_id, authorization.source_attempt_number, record.segment_id
        )
        source_record = source.record
        resolution = source.resolution
        if (
            authorization.job_id != record.job_id
            or authorization.source_attempt_number != record.attempt_number
            or authorization.target_attempt_number != record.attempt_number + 1
            or authorization.segment_id != record.segment_id
            or source_record != record
            or source_record.status is not RemoteSpeechJobStatus.UNCERTAIN
            or source_record.request_fingerprint
            != authorization.original_request_fingerprint
            or not source.matches_record_hash(
                authorization.original_uncertain_record_sha256
            )
            or resolution.job_id != authorization.job_id
            or resolution.attempt_number != authorization.source_attempt_number
            or resolution.scene_id != authorization.scene_id
            or resolution.segment_id != authorization.segment_id
            or resolution.request_fingerprint
            != authorization.original_request_fingerprint
            or resolution.fingerprint != authorization.unresolved_resolution_fingerprint
            or resolution.resolution is not SpeechSubmissionResolutionStatus.UNRESOLVED
            or consumption.job_id != authorization.job_id
            or consumption.target_attempt_number != authorization.target_attempt_number
            or consumption.segment_id != authorization.segment_id
            or consumption.authorization_fingerprint != authorization.fingerprint
            or consumption.replacement_submission_identity
            != replacement_submission_identity(authorization)
        ):
            return False
        return any(
            candidate.attempt_number == authorization.target_attempt_number
            and candidate.segment_id == record.segment_id
            and candidate.metadata.get("replacement_submission_identity")
            == consumption.replacement_submission_identity
            for candidate in job_records
        )

    def _source(
        self, job_id: UUID, source_attempt: int, segment_id: str
    ) -> _SpeechReplacementSource:
        record_content = self._read(
            self._remote_record_path(job_id, source_attempt, segment_id)
        )
        resolution_content = self._read(
            self._resolution_path(job_id, source_attempt, segment_id)
        )
        record = deserialize_remote_speech_job(record_content)
        resolution = deserialize_speech_submission_resolution(resolution_content)
        if (
            record.job_id != job_id
            or record.attempt_number != source_attempt
            or record.segment_id != segment_id
            or resolution.job_id != job_id
            or resolution.attempt_number != source_attempt
            or resolution.segment_id != segment_id
            or resolution.request_fingerprint != record.request_fingerprint
        ):
            raise SpeechUncertaintyResolutionError(
                "replacement source artifact identity differs"
            )
        return _SpeechReplacementSource(
            record=record,
            resolution=resolution,
            durable_record_sha256=hashlib.sha256(record_content).hexdigest(),
            canonical_record_sha256=hashlib.sha256(
                serialize_remote_speech_job(record)
            ).hexdigest(),
        )

    def _remote_record_path(self, job_id: UUID, attempt: int, segment: str) -> Path:
        return self._resolve(
            f"production/{job_id}/generating_narration/attempt-{attempt}/"
            f"remote-speech-jobs/{segment}.json"
        )

    def _resolution_path(self, job_id: UUID, attempt: int, segment: str) -> Path:
        return self._resolve(
            f"production/{job_id}/generating_narration/attempt-{attempt}/"
            f"speech-resolutions/{segment}.json"
        )

    def _authorization_path(
        self, value: SpeechUnresolvedReplacementAuthorization
    ) -> Path:
        return self._authorization_path_for(
            value.job_id, value.target_attempt_number, value.segment_id
        )

    def _authorization_path_for(self, job_id: UUID, attempt: int, segment: str) -> Path:
        return self._resolve(
            f"production/{job_id}/generating_narration/attempt-{attempt}/"
            f"speech-ra/{segment}.json"
        )

    def _consumption_path(self, value: SpeechUnresolvedReplacementConsumption) -> Path:
        return self._consumption_path_for(
            value.job_id, value.target_attempt_number, value.segment_id
        )

    def _consumption_path_for(self, job_id: UUID, attempt: int, segment: str) -> Path:
        return self._resolve(
            f"production/{job_id}/generating_narration/attempt-{attempt}/"
            f"speech-rc/{segment}.json"
        )

    def _resolve(self, relative: str) -> Path:
        try:
            return self._confinement.resolve(relative)
        except Exception as exc:
            raise SpeechUncertaintyResolutionError("replacement path is unsafe") from exc

    def _read(self, path: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(path)
            content = path.read_bytes()
            if not content or len(content) > self._maximum:
                raise ValueError("replacement artifact size is invalid")
            return content
        except Exception as exc:
            raise SpeechUncertaintyResolutionError("replacement source artifact is invalid") from exc

    def _write_once(self, path: Path, content: bytes) -> None:
        if not content or len(content) > self._maximum:
            raise SpeechUncertaintyResolutionError("replacement artifact size is invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(path.parent)
        lock = path.with_name(f".rr-{path.stem[-12:]}.lock")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            descriptor = -1
            if path.exists():
                if self._read(path) == content:
                    return
                raise SpeechUncertaintyResolutionError("conflicting replacement artifact exists")
            descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".r-")
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        except FileExistsError as exc:
            raise SpeechUncertaintyResolutionError("replacement artifact is locked") from exc
        except SpeechUncertaintyResolutionError:
            raise
        except OSError as exc:
            raise SpeechUncertaintyResolutionError("replacement artifact could not be persisted") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            with suppress(OSError):
                lock.unlink()


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _serialize(value: ContractModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _deserialize(content: bytes) -> Any:
    return json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )


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
    "SpeechUnresolvedReplacementAuthorization",
    "SpeechUnresolvedReplacementConsumption",
    "SpeechUnresolvedReplacementPermit",
    "SpeechUnresolvedReplacementStore",
    "replacement_submission_identity",
]
