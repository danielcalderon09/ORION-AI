"""Durable provider-neutral remote speech job contracts and transitions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.cost import (
    SpeechCostAuthorization,
    SpeechCostEstimate,
    SpeechReportedCost,
)
from backend.src.production.speech_generation.fingerprinting import (
    SpeechRemoteRequestFingerprintInput,
    speech_remote_request_fingerprint,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechAudioFormat,
    SpeechRemoteGenerationMode,
)

SUPPORTED_REMOTE_SPEECH_JOB_VERSIONS = frozenset({"1.0.0"})


class RemoteSpeechJobStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNCERTAIN = "uncertain"


REMOTE_SPEECH_TERMINAL_STATUSES = frozenset(
    {
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.CANCELLED,
        RemoteSpeechJobStatus.EXPIRED,
    }
)


class RemoteSpeechOutputExpectation(ContractModel):
    audio_format: SpeechAudioFormat
    mime_type: str = Field(min_length=1, max_length=100)
    extension: str = Field(pattern=r"^[a-z0-9]{2,8}$")
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channel_count: int = Field(ge=1, le=8)
    maximum_duration_ms: int = Field(gt=0, le=7_200_000)
    maximum_audio_bytes: int = Field(gt=0, le=250_000_000)


class RemoteSpeechOutputMetadata(ContractModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=250_000_000)
    duration_ms: int = Field(gt=0, le=7_200_000)
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channel_count: int = Field(ge=1, le=8)
    mime_type: str = Field(min_length=1, max_length=100)
    downloaded_at: datetime

    @field_validator("downloaded_at")
    @classmethod
    def aware_downloaded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("remote speech download time must be timezone-aware")
        return value


class RemoteSpeechSubmissionCheckpoint(ContractModel):
    status: RemoteSpeechJobStatus
    checkpointed_at: datetime
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("checkpointed_at")
    @classmethod
    def aware_checkpoint(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("remote speech checkpoint time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def submission_status(self) -> RemoteSpeechSubmissionCheckpoint:
        if self.status not in {
            RemoteSpeechJobStatus.PREPARED,
            RemoteSpeechJobStatus.SUBMITTING,
        }:
            raise ValueError("remote speech submission checkpoint status is invalid")
        return self


class RemoteSpeechTransportDiagnostic(ContractModel):
    """Sanitized diagnostics for an ambiguous transport interruption."""

    timeout_seconds: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    exception_class: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    elapsed_seconds: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=6
    )
    endpoint_family: str = Field(pattern=r"^[a-z0-9_]{1,100}$")


class RemoteSpeechJobRecord(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    job_id: UUID
    attempt_number: int = Field(ge=1)
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    voice: str = Field(min_length=1, max_length=200)
    language: str = Field(min_length=2, max_length=32)
    speaking_rate: Decimal | None = Field(default=None, gt=0, le=4)
    generation_mode: SpeechRemoteGenerationMode
    source_script_artifact_id: UUID
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=200)
    remote_job_id: str | None = Field(default=None, min_length=1, max_length=300)
    remote_generation_id: str | None = Field(default=None, min_length=1, max_length=300)
    status: RemoteSpeechJobStatus
    prepared_at: datetime
    submission_started_at: datetime | None = None
    submitted_at: datetime | None = None
    last_polled_at: datetime | None = None
    terminal_at: datetime | None = None
    poll_attempts: int = Field(default=0, ge=0, le=100_000)
    capability_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    pricing_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimated_cost: SpeechCostEstimate
    authorization: SpeechCostAuthorization | None = None
    reported_cost: SpeechReportedCost | None = None
    output_expectation: RemoteSpeechOutputExpectation
    output: RemoteSpeechOutputMetadata | None = None
    fresh_submission_permitted: bool
    safe_error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    transport_diagnostic: RemoteSpeechTransportDiagnostic | None = None
    options: dict[str, bool | int | str] = Field(default_factory=dict)
    metadata: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator(
        "prepared_at",
        "submission_started_at",
        "submitted_at",
        "last_polled_at",
        "terminal_at",
    )
    @classmethod
    def aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("remote speech timestamps must be timezone-aware")
        return value

    @field_validator("speaking_rate", mode="before")
    @classmethod
    def reject_float_rate(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("remote speech speaking rate must not use float")
        return value

    @field_validator("options", "metadata")
    @classmethod
    def safe_json_fields(cls, value: dict[str, bool | int | str]) -> dict[str, bool | int | str]:
        checked = validate_safe_json(value, path="remote_speech_job.safe_json")
        if not isinstance(checked, dict):
            raise ValueError("remote speech job safe JSON field must be an object")
        return checked

    @field_validator("idempotency_key", "remote_job_id", "remote_generation_id")
    @classmethod
    def safe_remote_identity(cls, value: str | None) -> str | None:
        if value is not None and any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
            for character in value
        ):
            raise ValueError("remote speech identity contains unsafe characters")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> RemoteSpeechJobRecord:
        if self.schema_version not in SUPPORTED_REMOTE_SPEECH_JOB_VERSIONS:
            raise ValueError("unsupported remote speech job schema version")
        if self.generation_mode is SpeechRemoteGenerationMode.STREAMING:
            raise ValueError("streaming speech execution is not implemented")
        if self.generation_mode is SpeechRemoteGenerationMode.SYNCHRONOUS and self.status in {
            RemoteSpeechJobStatus.SUBMITTED,
            RemoteSpeechJobStatus.PENDING,
            RemoteSpeechJobStatus.PROCESSING,
        }:
            raise ValueError("synchronous speech cannot enter polling states")
        if self.estimated_cost.provider != self.provider:
            raise ValueError("remote speech estimate provider differs")
        if (
            self.estimated_cost.model != self.model
            or self.estimated_cost.voice != self.voice
            or self.estimated_cost.pricing_snapshot_hash != self.pricing_snapshot_hash
        ):
            raise ValueError("remote speech estimate identity differs")
        if self.authorization is not None and (
            self.authorization.currency != self.estimated_cost.currency
            or self.authorization.maximum_authorized_cost
            < self.estimated_cost.estimated_maximum_cost
        ):
            raise ValueError("remote speech authorization does not cover estimate")
        if (
            self.reported_cost is not None
            and self.reported_cost.currency != self.estimated_cost.currency
        ):
            raise ValueError("reported speech cost currency differs")
        if self.estimated_cost.pricing_snapshot_at > self.prepared_at:
            raise ValueError("remote speech pricing snapshot postdates preparation")
        if self.authorization is not None and self.authorization.authorized_at > self.prepared_at:
            raise ValueError("remote speech authorization postdates preparation")
        expected_fingerprint = speech_remote_request_fingerprint(
            SpeechRemoteRequestFingerprintInput(
                source_script_artifact_id=self.source_script_artifact_id,
                source_script_sha256=self.source_script_sha256,
                segment_id=self.segment_id,
                normalized_text_hash=self.normalized_text_hash,
                provider=self.provider,
                model=self.model,
                voice=self.voice,
                language=self.language,
                speaking_rate=self.speaking_rate,
                audio_format=self.output_expectation.audio_format,
                sample_rate_hz=self.output_expectation.sample_rate_hz,
                channel_count=self.output_expectation.channel_count,
                capability_snapshot_hash=self.capability_snapshot_hash,
                pricing_snapshot_hash=self.pricing_snapshot_hash,
                generation_mode=self.generation_mode,
                options=self.options,
            )
        )
        if expected_fingerprint != self.request_fingerprint:
            raise ValueError("remote speech request fingerprint differs")
        if self.status is RemoteSpeechJobStatus.PREPARED:
            if (
                any(
                    value is not None
                    for value in (
                        self.submission_started_at,
                        self.submitted_at,
                        self.remote_job_id,
                        self.remote_generation_id,
                        self.terminal_at,
                    )
                )
                or not self.fresh_submission_permitted
            ):
                raise ValueError("prepared remote speech job is inconsistent")
        else:
            if self.submission_started_at is None or self.fresh_submission_permitted:
                raise ValueError("remote speech submission checkpoint is missing")
        if self.status is RemoteSpeechJobStatus.SUBMITTING and (
            self.submitted_at is not None
            or self.remote_job_id is not None
            or self.remote_generation_id is not None
        ):
            raise ValueError("submitting remote speech job already has remote identity")
        post_submission = {
            RemoteSpeechJobStatus.SUBMITTED,
            RemoteSpeechJobStatus.PENDING,
            RemoteSpeechJobStatus.PROCESSING,
            RemoteSpeechJobStatus.COMPLETED,
            RemoteSpeechJobStatus.FAILED,
            RemoteSpeechJobStatus.CANCELLED,
            RemoteSpeechJobStatus.EXPIRED,
        }
        if self.status in post_submission and self.submitted_at is None:
            raise ValueError("remote speech submission time is missing")
        if (
            self.generation_mode is SpeechRemoteGenerationMode.ASYNCHRONOUS
            and self.status
            in {
                RemoteSpeechJobStatus.SUBMITTED,
                RemoteSpeechJobStatus.PENDING,
                RemoteSpeechJobStatus.PROCESSING,
                RemoteSpeechJobStatus.COMPLETED,
            }
            and self.remote_job_id is None
        ):
            raise ValueError("asynchronous remote speech identity is missing")
        terminal = self.status in REMOTE_SPEECH_TERMINAL_STATUSES
        if terminal != (self.terminal_at is not None):
            raise ValueError("remote speech terminal timestamp is inconsistent")
        if (self.poll_attempts == 0) != (self.last_polled_at is None):
            raise ValueError("remote speech poll count and timestamp differ")
        chronological = tuple(
            value
            for value in (
                self.submission_started_at,
                self.submitted_at,
                self.last_polled_at,
                self.terminal_at,
            )
            if value is not None
        )
        if any(
            later < earlier
            for earlier, later in zip(
                (self.prepared_at, *chronological),
                chronological,
                strict=False,
            )
        ):
            raise ValueError("remote speech timestamps are not monotonic")
        if self.output is not None:
            if self.status is not RemoteSpeechJobStatus.COMPLETED:
                raise ValueError("remote speech output requires completed status")
            if (
                self.output.mime_type != self.output_expectation.mime_type
                or self.output.sample_rate_hz != self.output_expectation.sample_rate_hz
                or self.output.channel_count != self.output_expectation.channel_count
                or self.output.size_bytes > self.output_expectation.maximum_audio_bytes
                or self.output.duration_ms > self.output_expectation.maximum_duration_ms
            ):
                raise ValueError("remote speech output differs from expectations")
            if self.terminal_at is not None and self.output.downloaded_at < self.terminal_at:
                raise ValueError("remote speech output predates completion")
        return self


class RemoteSpeechJobSummary(ContractModel):
    status: RemoteSpeechJobStatus
    poll_attempts: int = Field(ge=0)
    estimated_maximum_cost: str
    has_remote_identity: bool
    has_verified_output: bool


_ALLOWED_REMOTE_SPEECH_TRANSITIONS = {
    RemoteSpeechJobStatus.PREPARED: {
        RemoteSpeechJobStatus.PREPARED,
        RemoteSpeechJobStatus.SUBMITTING,
    },
    RemoteSpeechJobStatus.SUBMITTING: {
        RemoteSpeechJobStatus.SUBMITTING,
        RemoteSpeechJobStatus.SUBMITTED,
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.UNCERTAIN,
    },
    RemoteSpeechJobStatus.SUBMITTED: {
        RemoteSpeechJobStatus.SUBMITTED,
        RemoteSpeechJobStatus.PENDING,
        RemoteSpeechJobStatus.PROCESSING,
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.CANCELLED,
        RemoteSpeechJobStatus.EXPIRED,
    },
    RemoteSpeechJobStatus.PENDING: {
        RemoteSpeechJobStatus.PENDING,
        RemoteSpeechJobStatus.PROCESSING,
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.CANCELLED,
        RemoteSpeechJobStatus.EXPIRED,
    },
    RemoteSpeechJobStatus.PROCESSING: {
        RemoteSpeechJobStatus.PROCESSING,
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.CANCELLED,
        RemoteSpeechJobStatus.EXPIRED,
    },
    RemoteSpeechJobStatus.COMPLETED: {RemoteSpeechJobStatus.COMPLETED},
    RemoteSpeechJobStatus.FAILED: {RemoteSpeechJobStatus.FAILED},
    RemoteSpeechJobStatus.CANCELLED: {RemoteSpeechJobStatus.CANCELLED},
    RemoteSpeechJobStatus.EXPIRED: {RemoteSpeechJobStatus.EXPIRED},
    RemoteSpeechJobStatus.UNCERTAIN: {RemoteSpeechJobStatus.UNCERTAIN},
}


def validate_remote_speech_job_transition(
    previous: RemoteSpeechJobRecord,
    current: RemoteSpeechJobRecord,
) -> None:
    current = RemoteSpeechJobRecord.model_validate(current.model_dump(mode="python"))
    immutable_fields = (
        "schema_version",
        "job_id",
        "attempt_number",
        "segment_id",
        "provider",
        "model",
        "voice",
        "language",
        "speaking_rate",
        "generation_mode",
        "source_script_artifact_id",
        "source_script_sha256",
        "normalized_text_hash",
        "request_fingerprint",
        "idempotency_key",
        "prepared_at",
        "capability_snapshot_hash",
        "pricing_snapshot_hash",
        "estimated_cost",
        "authorization",
        "output_expectation",
        "options",
    )
    if any(getattr(previous, field) != getattr(current, field) for field in immutable_fields):
        raise ValueError("remote speech immutable request identity changed")
    if previous.status in REMOTE_SPEECH_TERMINAL_STATUSES and current.status is not previous.status:
        raise ValueError("remote speech terminal status cannot change")
    if current.status not in _ALLOWED_REMOTE_SPEECH_TRANSITIONS[previous.status]:
        raise ValueError(
            f"invalid remote speech transition: {previous.status.value} -> {current.status.value}"
        )
    for field in ("remote_job_id", "remote_generation_id", "reported_cost", "output"):
        before = getattr(previous, field)
        after = getattr(current, field)
        if before is not None and after != before:
            raise ValueError(f"remote speech {field} cannot change")
    if current.poll_attempts < previous.poll_attempts:
        raise ValueError("remote speech poll attempts cannot decrease")
    if previous.last_polled_at is not None and (
        current.last_polled_at is None or current.last_polled_at < previous.last_polled_at
    ):
        raise ValueError("remote speech poll timestamp cannot decrease")
    if previous.status in REMOTE_SPEECH_TERMINAL_STATUSES:
        allowed_updates = previous.model_copy(
            update={
                "output": current.output,
                "reported_cost": current.reported_cost,
            }
        )
        if current != allowed_updates:
            raise ValueError("remote speech terminal record mutated")
