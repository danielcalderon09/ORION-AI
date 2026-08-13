"""Durable job-scoped authorization for one bounded speech recovery request."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
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
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.speech_generation.exceptions import SpeechGenerationError
from backend.src.production.speech_generation.remote_serialization import (
    deserialize_remote_speech_job,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
)


class SpeechRetryBudgetAuthorizationError(SpeechGenerationError):
    """Fail-closed speech retry-budget authorization failure."""


class SpeechRetryBudgetSourceRecord(ContractModel):
    attempt_number: int = Field(ge=1)
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    durable_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpeechRetryBudgetAuthorization(ContractModel):
    """One immutable overlay over one exhausted job's base speech limits."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    stage: Literal[ProductionStage.GENERATING_NARRATION] = (
        ProductionStage.GENERATING_NARRATION
    )
    source_stage_attempt: int = Field(ge=1)
    target_stage_attempt: int = Field(ge=2)
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_remote_records: tuple[SpeechRetryBudgetSourceRecord, ...] = Field(
        min_length=1,
        max_length=50,
    )
    source_remote_records_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_durable_request_count: int = Field(ge=1, le=50)
    current_accounted_tts_cost_usd: Decimal = Field(ge=0, decimal_places=9)
    base_maximum_requests_per_job: int = Field(ge=1, le=50)
    base_maximum_tts_job_cost_usd: Decimal = Field(gt=0, decimal_places=9)
    new_maximum_requests_per_job: int = Field(ge=2, le=51)
    new_maximum_tts_job_cost_usd: Decimal = Field(gt=0, decimal_places=9)
    maximum_additional_provider_requests: Literal[1]
    maximum_additional_estimated_cost_usd: Decimal = Field(gt=0, decimal_places=9)
    estimated_cost_per_request_usd: Decimal = Field(gt=0, decimal_places=9)
    operator_id: str = Field(min_length=1, max_length=100)
    authorized_at: datetime
    reason: Literal["operator_authorized_exhausted_speech_recovery"] = (
        "operator_authorized_exhausted_speech_recovery"
    )
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def create(cls, **values: Any) -> SpeechRetryBudgetAuthorization:
        candidate = cls.model_construct(fingerprint="0" * 64, **values)
        return cls.model_validate(
            candidate.model_copy(
                update={"fingerprint": speech_retry_budget_fingerprint(candidate)}
            ).model_dump(mode="python")
        )

    @field_validator(
        "current_accounted_tts_cost_usd",
        "base_maximum_tts_job_cost_usd",
        "new_maximum_tts_job_cost_usd",
        "maximum_additional_estimated_cost_usd",
        "estimated_cost_per_request_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech retry budget money must use Decimal text")
        return value

    @field_validator("operator_id")
    @classmethod
    def safe_operator(cls, value: str) -> str:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:"
        if any(character not in allowed for character in value):
            raise ValueError("speech retry budget operator identity is unsafe")
        return value

    @field_validator("authorized_at")
    @classmethod
    def aware_authorized_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech retry budget time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> SpeechRetryBudgetAuthorization:
        if self.target_stage_attempt != self.source_stage_attempt + 1:
            raise ValueError("speech retry budget must target the next stage attempt")
        if self.current_durable_request_count > self.base_maximum_requests_per_job:
            raise ValueError("speech retry source already exceeds the base request limit")
        if self.current_accounted_tts_cost_usd > self.base_maximum_tts_job_cost_usd:
            raise ValueError("speech retry source already exceeds the base cost limit")
        if self.new_maximum_requests_per_job != (
            self.base_maximum_requests_per_job
            + self.maximum_additional_provider_requests
        ):
            raise ValueError("speech retry request ceiling increase is inconsistent")
        if self.new_maximum_tts_job_cost_usd != (
            self.base_maximum_tts_job_cost_usd
            + self.maximum_additional_estimated_cost_usd
        ):
            raise ValueError("speech retry cost ceiling increase is inconsistent")
        if self.maximum_additional_estimated_cost_usd != (
            self.maximum_additional_provider_requests
            * self.estimated_cost_per_request_usd
        ):
            raise ValueError("speech retry additional exposure is inconsistent")
        if self.current_durable_request_count != len(self.source_remote_records):
            raise ValueError("speech retry source request count is inconsistent")
        canonical = tuple(
            sorted(
                self.source_remote_records,
                key=lambda item: (
                    item.attempt_number,
                    item.segment_id,
                    item.request_fingerprint,
                ),
            )
        )
        if self.source_remote_records != canonical:
            raise ValueError("speech retry source records are not canonical")
        if len({item.request_fingerprint for item in canonical}) != len(canonical):
            raise ValueError("speech retry source request identities are ambiguous")
        if self.source_remote_records_fingerprint != _source_records_fingerprint(canonical):
            raise ValueError("speech retry source records fingerprint differs")
        if self.fingerprint != speech_retry_budget_fingerprint(self):
            raise ValueError("speech retry budget fingerprint differs")
        return self


@dataclass(frozen=True, slots=True)
class SpeechRetryBudgetLimits:
    maximum_requests_per_job: int
    maximum_tts_job_cost_usd: Decimal
    authorization: SpeechRetryBudgetAuthorization | None = None


@dataclass(frozen=True, slots=True)
class _SpeechRetryEvidence:
    manifest_sha256: str
    records: tuple[SpeechRetryBudgetSourceRecord, ...]
    request_count: int
    accounted_cost_usd: Decimal


class FilesystemSpeechRetryBudgetAuthorizationStore:
    """Create and validate one bounded speech budget overlay per retry attempt."""

    def __init__(self, workspace_root: Path, *, maximum_bytes: int = 200_000) -> None:
        if not 4_096 <= maximum_bytes <= 1_000_000:
            raise ValueError("speech retry authorization size limit is invalid")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = maximum_bytes

    async def authorize(
        self,
        *,
        job_id: UUID,
        job_status: ProductionJobStatus,
        current_stage: ProductionStage,
        error_code: str | None,
        source_stage_attempt: int,
        target_stage_attempt: int,
        base_maximum_requests_per_job: int,
        base_maximum_tts_job_cost_usd: Decimal,
        new_maximum_requests_per_job: int,
        new_maximum_tts_job_cost_usd: Decimal,
        maximum_additional_provider_requests: int,
        maximum_additional_estimated_cost_usd: Decimal,
        estimated_cost_per_request_usd: Decimal,
        operator_id: str,
        clock: Callable[[], datetime],
    ) -> tuple[SpeechRetryBudgetAuthorization, bool]:
        return await asyncio.to_thread(
            self._authorize_sync,
            job_id,
            job_status,
            current_stage,
            error_code,
            source_stage_attempt,
            target_stage_attempt,
            base_maximum_requests_per_job,
            base_maximum_tts_job_cost_usd,
            new_maximum_requests_per_job,
            new_maximum_tts_job_cost_usd,
            maximum_additional_provider_requests,
            maximum_additional_estimated_cost_usd,
            estimated_cost_per_request_usd,
            operator_id,
            clock,
        )

    async def effective_limits(
        self,
        *,
        job_id: UUID,
        target_stage_attempt: int,
        base_maximum_requests_per_job: int,
        base_maximum_tts_job_cost_usd: Decimal,
        estimated_cost_per_request_usd: Decimal,
    ) -> SpeechRetryBudgetLimits:
        return await asyncio.to_thread(
            self._effective_limits_sync,
            job_id,
            target_stage_attempt,
            base_maximum_requests_per_job,
            base_maximum_tts_job_cost_usd,
            estimated_cost_per_request_usd,
        )

    def _authorize_sync(
        self,
        job_id: UUID,
        job_status: ProductionJobStatus,
        current_stage: ProductionStage,
        error_code: str | None,
        source_attempt: int,
        target_attempt: int,
        base_requests: int,
        base_cost: Decimal,
        new_requests: int,
        new_cost: Decimal,
        additional_requests: int,
        additional_cost: Decimal,
        estimated_per_request: Decimal,
        operator_id: str,
        clock: Callable[[], datetime],
    ) -> tuple[SpeechRetryBudgetAuthorization, bool]:
        if job_status not in {
            ProductionJobStatus.FAILED,
            ProductionJobStatus.NEEDS_USER_ACTION,
        }:
            raise SpeechRetryBudgetAuthorizationError(
                "job is not stopped for operator recovery"
            )
        if current_stage is not ProductionStage.GENERATING_NARRATION:
            raise SpeechRetryBudgetAuthorizationError("job is not in narration recovery")
        if error_code not in {
            "speech_generation_invalid",
            "speech_request_limit_exhausted",
            "speech_cost_limit_exhausted",
        }:
            raise SpeechRetryBudgetAuthorizationError(
                "job failure is not a speech budget exhaustion"
            )
        if target_attempt != source_attempt + 1:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry target differs from the recovery lineage"
            )
        evidence = self._evidence(job_id, source_attempt)
        if evidence.request_count > base_requests or evidence.accounted_cost_usd > base_cost:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry source exceeds its base budget"
            )
        if not (
            evidence.request_count >= base_requests
            or evidence.accounted_cost_usd + estimated_per_request > base_cost
        ):
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry source has not exhausted its base budget"
            )
        try:
            candidate = SpeechRetryBudgetAuthorization.create(
                job_id=job_id,
                source_stage_attempt=source_attempt,
                target_stage_attempt=target_attempt,
                source_manifest_sha256=evidence.manifest_sha256,
                source_remote_records=evidence.records,
                source_remote_records_fingerprint=_source_records_fingerprint(
                    evidence.records
                ),
                current_durable_request_count=evidence.request_count,
                current_accounted_tts_cost_usd=evidence.accounted_cost_usd,
                base_maximum_requests_per_job=base_requests,
                base_maximum_tts_job_cost_usd=base_cost,
                new_maximum_requests_per_job=new_requests,
                new_maximum_tts_job_cost_usd=new_cost,
                maximum_additional_provider_requests=additional_requests,
                maximum_additional_estimated_cost_usd=additional_cost,
                estimated_cost_per_request_usd=estimated_per_request,
                operator_id=operator_id,
                authorized_at=clock(),
            )
        except ValueError as exc:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry requested capacity is invalid"
            ) from exc
        path = self._authorization_path(job_id, target_attempt)
        existing = self._read_path(path)
        if existing is not None:
            self._validate_authorization(
                existing,
                expected_job_id=job_id,
                evidence=evidence,
                target_attempt=target_attempt,
                base_requests=base_requests,
                base_cost=base_cost,
                estimated_per_request=estimated_per_request,
                allow_authorized_growth=False,
            )
            comparable = (
                "new_maximum_requests_per_job",
                "new_maximum_tts_job_cost_usd",
                "maximum_additional_provider_requests",
                "maximum_additional_estimated_cost_usd",
                "operator_id",
            )
            if all(getattr(existing, name) == getattr(candidate, name) for name in comparable):
                return existing, True
            raise SpeechRetryBudgetAuthorizationError(
                "a conflicting speech retry budget authorization exists"
            )
        self._atomic_create(path, serialize_speech_retry_budget(candidate))
        return candidate, False

    def _effective_limits_sync(
        self,
        job_id: UUID,
        target_attempt: int,
        base_requests: int,
        base_cost: Decimal,
        estimated_per_request: Decimal,
    ) -> SpeechRetryBudgetLimits:
        authorization = self._read_path(self._authorization_path(job_id, target_attempt))
        if authorization is None:
            return SpeechRetryBudgetLimits(base_requests, base_cost)
        evidence = self._evidence(job_id, authorization.source_stage_attempt)
        self._validate_authorization(
            authorization,
            expected_job_id=job_id,
            evidence=evidence,
            target_attempt=target_attempt,
            base_requests=base_requests,
            base_cost=base_cost,
            estimated_per_request=estimated_per_request,
            allow_authorized_growth=True,
        )
        return SpeechRetryBudgetLimits(
            authorization.new_maximum_requests_per_job,
            authorization.new_maximum_tts_job_cost_usd,
            authorization,
        )

    def _validate_authorization(
        self,
        authorization: SpeechRetryBudgetAuthorization,
        *,
        expected_job_id: UUID,
        evidence: _SpeechRetryEvidence,
        target_attempt: int,
        base_requests: int,
        base_cost: Decimal,
        estimated_per_request: Decimal,
        allow_authorized_growth: bool,
    ) -> None:
        if (
            authorization.job_id != expected_job_id
            or authorization.target_stage_attempt != target_attempt
            or authorization.base_maximum_requests_per_job != base_requests
            or authorization.base_maximum_tts_job_cost_usd != base_cost
            or authorization.estimated_cost_per_request_usd != estimated_per_request
            or authorization.source_manifest_sha256 != evidence.manifest_sha256
        ):
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget authorization drifted"
            )
        current_by_identity = {
            item.request_fingerprint: item for item in evidence.records
        }
        if any(
            current_by_identity.get(item.request_fingerprint) != item
            for item in authorization.source_remote_records
        ):
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget source records drifted"
            )
        maximum_count = (
            authorization.new_maximum_requests_per_job
            if allow_authorized_growth
            else authorization.current_durable_request_count
        )
        maximum_cost = (
            authorization.new_maximum_tts_job_cost_usd
            if allow_authorized_growth
            else authorization.current_accounted_tts_cost_usd
        )
        if not (
            authorization.current_durable_request_count
            <= evidence.request_count
            <= maximum_count
        ) or not (
            authorization.current_accounted_tts_cost_usd
            <= evidence.accounted_cost_usd
            <= maximum_cost
        ):
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget durable state drifted"
            )

    def _evidence(self, job_id: UUID, source_attempt: int) -> _SpeechRetryEvidence:
        manifest_path = self._resolve(
            f"production/{job_id}/generating_narration/attempt-{source_attempt}/"
            "speech-generation-manifest.json"
        )
        manifest_content = self._read(manifest_path)
        manifest = deserialize_speech_manifest(manifest_content)
        if manifest.job_id != job_id or manifest.attempt_number != source_attempt:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry source manifest identity differs"
            )
        base = self._resolve(f"production/{job_id}/generating_narration")
        records: list[SpeechRetryBudgetSourceRecord] = []
        accounted = Decimal(0)
        for path in sorted(base.glob("attempt-*/remote-speech-jobs/segment-*.json")):
            content = self._read(path)
            record = deserialize_remote_speech_job(content)
            expected = self._resolve(
                f"production/{job_id}/generating_narration/attempt-{record.attempt_number}/"
                f"remote-speech-jobs/{record.segment_id}.json"
            )
            if record.job_id != job_id or expected != path:
                raise SpeechRetryBudgetAuthorizationError(
                    "speech retry remote record identity differs"
                )
            records.append(
                SpeechRetryBudgetSourceRecord(
                    attempt_number=record.attempt_number,
                    segment_id=record.segment_id,
                    request_fingerprint=record.request_fingerprint,
                    durable_sha256=hashlib.sha256(content).hexdigest(),
                )
            )
            if record.submission_started_at is not None:
                accounted += (
                    record.reported_cost.amount
                    if record.reported_cost is not None
                    else record.estimated_cost.estimated_maximum_cost
                )
        canonical = tuple(
            sorted(
                records,
                key=lambda item: (
                    item.attempt_number,
                    item.segment_id,
                    item.request_fingerprint,
                ),
            )
        )
        return _SpeechRetryEvidence(
            manifest_sha256=hashlib.sha256(manifest_content).hexdigest(),
            records=canonical,
            request_count=len(canonical),
            accounted_cost_usd=accounted,
        )

    def _authorization_path(self, job_id: UUID, target_attempt: int) -> Path:
        return self._resolve(
            f"production/{job_id}/generating_narration/attempt-{target_attempt}/"
            "speech-retry-budget-authorization.json"
        )

    def _read_path(self, path: Path) -> SpeechRetryBudgetAuthorization | None:
        if not path.exists() and not path.is_symlink():
            return None
        try:
            return deserialize_speech_retry_budget(self._read(path))
        except (OSError, ValueError) as exc:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget authorization is invalid"
            ) from exc

    def _resolve(self, relative: str) -> Path:
        try:
            return self._confinement.resolve(relative)
        except Exception as exc:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget path is unsafe"
            ) from exc

    def _read(self, path: Path) -> bytes:
        try:
            self._confinement.reject_unsafe_file(path)
            content = path.read_bytes()
            if not content or len(content) > self._maximum:
                raise ValueError("speech retry artifact size is invalid")
            return content
        except Exception as exc:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry source artifact is invalid"
            ) from exc

    def _atomic_create(self, path: Path, content: bytes) -> None:
        if not content or len(content) > self._maximum:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget authorization size is invalid"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(path.parent)
        lock = path.with_name(".speech-retry-budget.lock")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            descriptor = -1
            descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".speech-budget-")
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists() or path.is_symlink():
                raise SpeechRetryBudgetAuthorizationError(
                    "speech retry budget authorization changed concurrently"
                )
            os.replace(temporary, path)
            temporary = None
        except FileExistsError as exc:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget authorization is locked"
            ) from exc
        except SpeechRetryBudgetAuthorizationError:
            raise
        except OSError as exc:
            raise SpeechRetryBudgetAuthorizationError(
                "speech retry budget authorization could not be persisted"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()
            with suppress(OSError):
                lock.unlink()


def speech_retry_budget_fingerprint(
    authorization: SpeechRetryBudgetAuthorization,
) -> str:
    return _fingerprint(authorization.model_dump(mode="json", exclude={"fingerprint"}))


def serialize_speech_retry_budget(
    authorization: SpeechRetryBudgetAuthorization,
) -> bytes:
    return (
        json.dumps(
            authorization.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def deserialize_speech_retry_budget(content: bytes) -> SpeechRetryBudgetAuthorization:
    return SpeechRetryBudgetAuthorization.model_validate(
        json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    )


def _source_records_fingerprint(
    records: tuple[SpeechRetryBudgetSourceRecord, ...],
) -> str:
    return _fingerprint([item.model_dump(mode="json") for item in records])


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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
    "FilesystemSpeechRetryBudgetAuthorizationStore",
    "SpeechRetryBudgetAuthorization",
    "SpeechRetryBudgetAuthorizationError",
    "SpeechRetryBudgetLimits",
    "deserialize_speech_retry_budget",
    "serialize_speech_retry_budget",
    "speech_retry_budget_fingerprint",
]
