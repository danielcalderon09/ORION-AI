"""Bounded narration fitting contracts and OpenRouter adapter."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path


class NarrationFittingError(RuntimeError):
    """Base safe fitting failure."""


class NarrationFittingConfigurationError(NarrationFittingError):
    pass


class NarrationFittingProviderError(NarrationFittingError):
    def __init__(
        self,
        message: str,
        *,
        safe_error_code: str = "provider_error",
        retryable: bool = False,
        http_status: int | None = None,
        provider_request_id: str | None = None,
        response_headers_received: bool = False,
        response_received: bool = False,
        provider_retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.safe_error_code = safe_error_code
        self.retryable = retryable
        self.http_status = http_status
        self.provider_request_id = provider_request_id
        self.response_headers_received = response_headers_received
        self.response_received = response_received
        self.provider_retry_count = provider_retry_count


class NarrationFittingTransientProviderError(NarrationFittingProviderError):
    pass


class NarrationFittingUncertainError(NarrationFittingProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, safe_error_code="uncertain_transport")


class NarrationFittingStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTING = "submitting"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class NarrationFittingStrategy(StrEnum):
    DETERMINISTIC_LOCAL = "deterministic_local"
    REMOTE_PROVIDER = "remote_provider"


class NarrationFittingConfiguration(ContractModel):
    provider: Literal["disabled", "openrouter"] = "disabled"
    model: str = "google/gemini-2.5-flash-lite"
    allow_billable_requests: bool = False
    maximum_attempts: int = Field(default=2, ge=0, le=5)
    estimated_cost_usd_per_attempt: Decimal | None = Field(default=None, gt=0)
    maximum_estimated_cost_usd_per_attempt: Decimal | None = Field(default=None, gt=0)
    maximum_estimated_job_cost_usd: Decimal | None = Field(default=None, gt=0)
    maximum_provider_retries: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_activation(self) -> NarrationFittingConfiguration:
        if self.provider == "openrouter":
            if not self.allow_billable_requests or self.maximum_attempts < 1:
                raise ValueError("OpenRouter narration fitting is not explicitly authorized")
            if (
                self.estimated_cost_usd_per_attempt is None
                or self.maximum_estimated_cost_usd_per_attempt is None
                or self.maximum_estimated_job_cost_usd is None
                or self.estimated_cost_usd_per_attempt > self.maximum_estimated_cost_usd_per_attempt
            ):
                raise ValueError("OpenRouter narration fitting cost authorization is invalid")
        elif self.allow_billable_requests or any(
            value is not None
            for value in (
                self.estimated_cost_usd_per_attempt,
                self.maximum_estimated_cost_usd_per_attempt,
                self.maximum_estimated_job_cost_usd,
            )
        ):
            raise ValueError("disabled narration fitting cannot authorize billing")
        return self


class NarrationFittingRequest(ContractModel):
    job_id: UUID
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    attempt_number: int = Field(ge=1, le=5)
    current_narration: str = Field(min_length=1, max_length=6_000, repr=False)
    current_duration_ms: int = Field(gt=0, le=600_000)
    target_duration_ms: int = Field(gt=0, le=600_000)
    language: str = Field(min_length=2, max_length=16)
    tone: str = Field(min_length=1, max_length=300)
    maximum_provider_retries: int = Field(default=1, ge=0, le=1)


class NarrationFittingResult(ContractModel):
    revised_narration: str = Field(min_length=3, max_length=6_000, repr=False)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: Decimal | None = Field(default=None, ge=0)
    finish_reason: str | None = Field(default=None, max_length=100)
    provider_retry_count: int = Field(default=0, ge=0, le=1)
    response_headers_received: bool = True
    response_received: bool = True


class LocalNarrationFittingResult(ContractModel):
    revised_narration: str = Field(min_length=3, max_length=6_000, repr=False)
    rules_applied: tuple[str, ...] = Field(min_length=1, max_length=32)


class LocalNarrationFitter(Protocol):
    name: str
    model: str

    def revise(self, request: NarrationFittingRequest) -> LocalNarrationFittingResult | None: ...


class NarrationFittingRecord(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    attempt_number: int = Field(ge=1, le=5)
    strategy: NarrationFittingStrategy = NarrationFittingStrategy.REMOTE_PROVIDER
    rules_applied: tuple[str, ...] = Field(default=(), max_length=32)
    previous_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    revised_text_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    revised_narration: str | None = Field(default=None, min_length=3, max_length=6_000, repr=False)
    previous_duration_ms: int = Field(gt=0, le=600_000)
    previous_audio_binary_asset_id: str = Field(
        pattern=r"^speech-segment-[a-f0-9]{32}$"
    )
    previous_audio_storage_path: str
    previous_audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_duration_ms: int = Field(gt=0, le=600_000)
    overrun_ms: int = Field(gt=0, le=600_000)
    overrun_ratio: Decimal = Field(gt=0, le=100)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    estimated_cost_usd: Decimal = Field(ge=0)
    maximum_authorized_cost_usd: Decimal = Field(ge=0)
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: NarrationFittingStatus
    fresh_submission_permitted: bool
    prepared_at: datetime
    submission_started_at: datetime | None = None
    terminal_at: datetime | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: Decimal | None = Field(default=None, ge=0)
    finish_reason: str | None = Field(default=None, max_length=100)
    safe_error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    retryable: bool | None = None
    response_headers_received: bool = False
    response_received: bool = False
    provider_retry_count: int = Field(default=0, ge=0, le=1)

    @field_validator("prepared_at", "submission_started_at", "terminal_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("narration fitting timestamps must be timezone-aware")
        return value

    @field_validator("previous_audio_storage_path")
    @classmethod
    def safe_audio_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if not normalized.endswith(".wav") or "\\" in normalized:
            raise ValueError("previous narration audio path is invalid")
        return normalized

    @field_validator("provider_request_id")
    @classmethod
    def safe_provider_request_id(cls, value: str | None) -> str | None:
        if value is not None and any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
            for character in value
        ):
            raise ValueError("narration fitting provider request identity is unsafe")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> NarrationFittingRecord:
        if self.estimated_cost_usd > self.maximum_authorized_cost_usd:
            raise ValueError("narration fitting estimate exceeds authorization")
        if self.strategy is NarrationFittingStrategy.DETERMINISTIC_LOCAL:
            if (
                self.provider != "deterministic_local"
                or self.model != "spanish_rules_v1"
                or self.estimated_cost_usd != 0
                or self.maximum_authorized_cost_usd != 0
                or not self.rules_applied
                or self.status is not NarrationFittingStatus.COMPLETED
                or self.fresh_submission_permitted
                or self.submission_started_at is not None
                or self.terminal_at is None
                or self.provider_request_id is not None
                or self.http_status is not None
                or self.reported_cost_usd is not None
                or self.provider_retry_count != 0
                or self.response_headers_received
                or self.response_received
            ):
                raise ValueError("local narration fitting lifecycle is inconsistent")
        else:
            if self.rules_applied:
                raise ValueError("remote narration fitting cannot contain local rules")
            if self.status is NarrationFittingStatus.PREPARED:
                if self.submission_started_at is not None or not self.fresh_submission_permitted:
                    raise ValueError("prepared narration fitting record is inconsistent")
            elif self.submission_started_at is None or self.fresh_submission_permitted:
                raise ValueError("submitted narration fitting record lacks checkpoint")
        terminal = self.status in {
            NarrationFittingStatus.COMPLETED,
            NarrationFittingStatus.FAILED,
            NarrationFittingStatus.UNCERTAIN,
        }
        if terminal != (self.terminal_at is not None):
            raise ValueError("narration fitting terminal timestamp is inconsistent")
        if self.status is NarrationFittingStatus.COMPLETED:
            if self.revised_narration is None or self.revised_text_hash is None:
                raise ValueError("completed narration fitting record has no revision")
            if narration_text_hash(self.revised_narration) != self.revised_text_hash:
                raise ValueError("revised narration hash differs")
        elif self.revised_narration is not None or self.revised_text_hash is not None:
            raise ValueError("unfinished narration fitting record cannot contain revised text")
        return self


class NarrationFittingProvider(Protocol):
    name: str
    model: str

    async def revise(self, request: NarrationFittingRequest) -> NarrationFittingResult: ...

    async def close(self) -> None: ...


class DisabledNarrationFittingProvider:
    name = "disabled"
    model = "disabled"

    async def revise(self, request: NarrationFittingRequest) -> NarrationFittingResult:
        raise NarrationFittingConfigurationError("narration fitting is disabled")

    async def close(self) -> None:
        return None


def narration_text_hash(value: str) -> str:
    return hashlib.sha256(" ".join(value.split()).encode("utf-8")).hexdigest()


def narration_fitting_fingerprint(request: NarrationFittingRequest, model: str) -> str:
    payload = {
        "schema_version": "1.0.0",
        "model": model,
        "scene_id": request.scene_id,
        "attempt_number": request.attempt_number,
        "previous_text_hash": narration_text_hash(request.current_narration),
        "current_duration_ms": request.current_duration_ms,
        "target_duration_ms": request.target_duration_ms,
        "language": request.language,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def local_narration_fitting_fingerprint(
    request: NarrationFittingRequest,
    *,
    revised_narration: str,
    rules_applied: tuple[str, ...],
    model: str,
) -> str:
    payload = {
        "schema_version": "1.0.0",
        "strategy": NarrationFittingStrategy.DETERMINISTIC_LOCAL.value,
        "model": model,
        "scene_id": request.scene_id,
        "attempt_number": request.attempt_number,
        "previous_text_hash": narration_text_hash(request.current_narration),
        "revised_text_hash": narration_text_hash(revised_narration),
        "current_duration_ms": request.current_duration_ms,
        "target_duration_ms": request.target_duration_ms,
        "language": request.language,
        "rules_applied": rules_applied,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_narration_revision(previous: str, revised: str) -> str:
    value = " ".join(revised.split()).strip()
    if len(value) < 12 or len(value.split()) < 3:
        raise NarrationFittingProviderError("revised narration is not meaningful")
    if narration_text_hash(value) == narration_text_hash(previous):
        raise NarrationFittingProviderError("revised narration did not change")
    if len(value) >= len(" ".join(previous.split())):
        raise NarrationFittingProviderError("revised narration is not shorter")
    return value


__all__ = [
    "DisabledNarrationFittingProvider",
    "NarrationFittingConfiguration",
    "NarrationFittingConfigurationError",
    "NarrationFittingError",
    "NarrationFittingProvider",
    "NarrationFittingProviderError",
    "NarrationFittingTransientProviderError",
    "NarrationFittingRecord",
    "NarrationFittingRequest",
    "NarrationFittingResult",
    "NarrationFittingStrategy",
    "NarrationFittingStatus",
    "NarrationFittingUncertainError",
    "LocalNarrationFitter",
    "LocalNarrationFittingResult",
    "local_narration_fitting_fingerprint",
    "narration_fitting_fingerprint",
    "narration_text_hash",
    "validate_narration_revision",
]
