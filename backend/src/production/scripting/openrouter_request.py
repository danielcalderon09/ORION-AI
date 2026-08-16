"""Durable identities and state for one controlled OpenRouter script request."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.serialization import serialize_production_script

SUPPORTED_OPENROUTER_SCRIPTING_REQUEST_VERSIONS = frozenset({"1.0.0"})


class OpenRouterScriptingRequestStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTING = "submitting"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class OpenRouterScriptingValidationErrorCode(StrEnum):
    PROVIDER_BODY_ERROR = "provider_body_error"
    PROVIDER_ENVELOPE_PROTOCOL = "provider_envelope_protocol"
    OUTPUT_TEXT_PROTOCOL = "output_text_protocol"
    INNER_JSON_PARSE = "inner_json_parse"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    PRODUCTION_SCRIPT_SCHEMA = "production_script_schema"
    PRODUCTION_SCRIPT_CONTRACT = "production_script_contract"
    PLAN_CONTRACT = "plan_contract"
    SCENE_COUNT_POLICY = "scene_count_policy"
    DURATION_POLICY = "duration_policy"
    NARRATION_COMPRESSION_CONTRACT = "narration_compression_contract"
    NARRATION_COMPRESSION_SCHEMA_INVALID = "narration_compression_schema_invalid"
    NARRATION_COMPRESSION_SCENE_MISSING = "narration_compression_scene_missing"
    NARRATION_COMPRESSION_SCENE_DUPLICATE = "narration_compression_scene_duplicate"
    NARRATION_COMPRESSION_SCENE_UNKNOWN = "narration_compression_scene_unknown"
    NARRATION_COMPRESSION_EMPTY_NARRATION = "narration_compression_empty_narration"
    NARRATION_COMPRESSION_UNSAFE_NARRATION = "narration_compression_unsafe_narration"
    NARRATION_COMPRESSION_SCENE_BELOW_MINIMUM_WORD_BUDGET = (
        "narration_compression_scene_below_minimum_word_budget"
    )
    NARRATION_COMPRESSION_SCENE_ABOVE_MAXIMUM_WORD_BUDGET = (
        "narration_compression_scene_above_maximum_word_budget"
    )
    NARRATION_COMPRESSION_BELOW_MINIMUM_WORD_BUDGET = (
        "narration_compression_below_minimum_word_budget"
    )
    NARRATION_COMPRESSION_ABOVE_MAXIMUM_WORD_BUDGET = (
        "narration_compression_above_maximum_word_budget"
    )
    NARRATION_COMPRESSION_SOURCE_MISMATCH = "narration_compression_source_mismatch"
    NARRATION_COMPRESSION_MERGE_INVALID = "narration_compression_merge_invalid"
    NARRATION_EXPANSION_CONTRACT = "narration_expansion_contract"
    NARRATION_EXPANSION_SCHEMA_INVALID = "narration_expansion_schema_invalid"
    NARRATION_EXPANSION_LANGUAGE_MISMATCH = "narration_expansion_language_mismatch"
    NARRATION_EXPANSION_SCENE_MISSING = "narration_expansion_scene_missing"
    NARRATION_EXPANSION_SCENE_DUPLICATE = "narration_expansion_scene_duplicate"
    NARRATION_EXPANSION_SCENE_UNKNOWN = "narration_expansion_scene_unknown"
    NARRATION_EXPANSION_EMPTY_NARRATION = "narration_expansion_empty_narration"
    NARRATION_EXPANSION_UNSAFE_NARRATION = "narration_expansion_unsafe_narration"
    NARRATION_EXPANSION_SCENE_BUDGET_EXCEEDED = (
        "narration_expansion_scene_budget_exceeded"
    )
    NARRATION_EXPANSION_GLOBAL_BUDGET_EXCEEDED = (
        "narration_expansion_global_budget_exceeded"
    )
    NARRATION_EXPANSION_SOURCE_MISMATCH = "narration_expansion_source_mismatch"
    NARRATION_EXPANSION_MERGE_INVALID = "narration_expansion_merge_invalid"
    UNSUPPORTED_FIELD = "unsupported_field"
    UNKNOWN_STRUCTURED_OUTPUT_ERROR = "unknown_structured_output_error"


class OpenRouterScriptingFingerprintInput(ContractModel):
    schema_version: str = "1.0.0"
    provider: Literal["openrouter"] = "openrouter"
    model: str = Field(min_length=1, max_length=300)
    source_prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_plan_artifact_id: UUID
    source_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    language: str = Field(min_length=2, max_length=16)
    target_duration_seconds: float = Field(gt=0, le=60)
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    scene_count: int = Field(ge=1, le=50)
    scripting_configuration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    script_schema_version: Literal["1.0.0"] = "1.0.0"
    prompt_template_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    prompt_template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    structured_output_mode: Literal["json_schema"] = "json_schema"
    temperature: Decimal = Field(ge=0, le=2, max_digits=4, decimal_places=3)
    max_output_tokens: int = Field(ge=1, le=100_000)
    request_purpose: Literal[
        "production_script", "narration_compression", "narration_expansion"
    ] | None = None
    source_script_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("temperature", mode="before")
    @classmethod
    def reject_float_temperature(cls, value: Any) -> Any:
        if isinstance(value, float):
            return Decimal(str(value))
        return value

    @model_validator(mode="after")
    def validate_request_purpose(self) -> OpenRouterScriptingFingerprintInput:
        if self.request_purpose in {"narration_compression", "narration_expansion"}:
            if self.source_script_sha256 is None:
                raise ValueError("narration revision requires a source script fingerprint")
        elif self.source_script_sha256 is not None:
            raise ValueError("production scripting cannot pin a source script fingerprint")
        return self


def openrouter_scripting_request_fingerprint(
    value: OpenRouterScriptingFingerprintInput,
) -> str:
    payload = value.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scripting_configuration_fingerprint(configuration: dict[str, object]) -> str:
    checked = validate_safe_json(configuration, path="scripting_configuration")
    encoded = json.dumps(
        checked,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class OpenRouterScriptingRequestRecord(ContractModel):
    schema_version: str = "1.0.0"
    job_id: UUID
    attempt_number: int = Field(ge=1)
    fingerprint_input: OpenRouterScriptingFingerprintInput
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: OpenRouterScriptingRequestStatus
    estimated_cost_usd: Decimal = Field(gt=0, max_digits=24, decimal_places=9)
    maximum_authorized_cost_usd: Decimal = Field(gt=0, max_digits=24, decimal_places=9)
    prepared_at: datetime
    submission_started_at: datetime | None = None
    terminal_at: datetime | None = None
    fresh_submission_permitted: bool
    safe_error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    validation_error_code: OpenRouterScriptingValidationErrorCode | None = None
    validation_error_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z0-9_.\[\]-]+$",
    )
    validation_error_message: str | None = Field(default=None, min_length=1, max_length=240)
    http_status: int | None = Field(default=None, ge=100, le=599)
    requested_model: str | None = Field(default=None, min_length=1, max_length=300)
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=200)
    reported_model: str | None = Field(default=None, min_length=1, max_length=300)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=24,
        decimal_places=9,
    )
    finish_reason: str | None = Field(default=None, min_length=1, max_length=100)
    script_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    script: ProductionScript | None = None
    metadata: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator(
        "estimated_cost_usd",
        "maximum_authorized_cost_usd",
        "reported_cost_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("OpenRouter scripting money must not use float")
        return value

    @field_validator("prepared_at", "submission_started_at", "terminal_at")
    @classmethod
    def require_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("OpenRouter scripting timestamps must be timezone-aware")
        return value

    @field_validator("provider_request_id")
    @classmethod
    def safe_provider_identity(cls, value: str | None) -> str | None:
        if value is not None and any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
            for character in value
        ):
            raise ValueError("OpenRouter scripting request identity is unsafe")
        return value

    @field_validator("validation_error_message")
    @classmethod
    def safe_validation_message(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("OpenRouter scripting validation message is unsafe")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(
        cls,
        value: dict[str, bool | int | str],
    ) -> dict[str, bool | int | str]:
        checked = validate_safe_json(value, path="openrouter_scripting.metadata")
        if not isinstance(checked, dict):
            raise ValueError("OpenRouter scripting metadata must be an object")
        return checked

    @model_validator(mode="after")
    def validate_record(self) -> OpenRouterScriptingRequestRecord:
        if self.schema_version not in SUPPORTED_OPENROUTER_SCRIPTING_REQUEST_VERSIONS:
            raise ValueError("unsupported OpenRouter scripting request schema")
        if (
            openrouter_scripting_request_fingerprint(self.fingerprint_input)
            != self.request_fingerprint
        ):
            raise ValueError("OpenRouter scripting request fingerprint differs")
        if self.estimated_cost_usd > self.maximum_authorized_cost_usd:
            raise ValueError("OpenRouter scripting estimate exceeds authorization")
        if (
            self.requested_model is not None
            and self.requested_model != self.fingerprint_input.model
        ):
            raise ValueError("OpenRouter scripting requested model differs")
        summary_values = (
            self.validation_error_path,
            self.validation_error_message,
        )
        if any(value is not None for value in summary_values) and self.validation_error_code is None:
            raise ValueError("OpenRouter scripting validation summary has no code")
        if (
            self.validation_error_code is not None
            and self.status is not OpenRouterScriptingRequestStatus.FAILED
        ):
            raise ValueError("OpenRouter scripting validation diagnosis requires failed status")
        if self.status is OpenRouterScriptingRequestStatus.PREPARED:
            if (
                self.submission_started_at is not None
                or self.terminal_at is not None
                or not self.fresh_submission_permitted
            ):
                raise ValueError("prepared OpenRouter scripting request is inconsistent")
        else:
            if self.submission_started_at is None or self.fresh_submission_permitted:
                raise ValueError("OpenRouter scripting submission checkpoint is missing")
        is_terminal = self.status in {
            OpenRouterScriptingRequestStatus.COMPLETED,
            OpenRouterScriptingRequestStatus.FAILED,
            OpenRouterScriptingRequestStatus.UNCERTAIN,
        }
        if is_terminal != (self.terminal_at is not None):
            raise ValueError("OpenRouter scripting terminal time is inconsistent")
        if self.submission_started_at is not None and self.submission_started_at < self.prepared_at:
            raise ValueError("OpenRouter scripting submission predates preparation")
        if (
            self.terminal_at is not None
            and self.submission_started_at is not None
            and self.terminal_at < self.submission_started_at
        ):
            raise ValueError("OpenRouter scripting terminal time predates submission")
        if self.status is OpenRouterScriptingRequestStatus.COMPLETED:
            if self.script is None or self.script_sha256 is None:
                raise ValueError("completed OpenRouter scripting request has no script")
            actual = hashlib.sha256(serialize_production_script(self.script)).hexdigest()
            if actual != self.script_sha256:
                raise ValueError("OpenRouter scripting script checksum differs")
        elif self.script is not None or self.script_sha256 is not None:
            raise ValueError("non-completed OpenRouter scripting request contains a script")
        return self


_ALLOWED_TRANSITIONS = {
    OpenRouterScriptingRequestStatus.PREPARED: {
        OpenRouterScriptingRequestStatus.SUBMITTING,
    },
    OpenRouterScriptingRequestStatus.SUBMITTING: {
        OpenRouterScriptingRequestStatus.COMPLETED,
        OpenRouterScriptingRequestStatus.FAILED,
        OpenRouterScriptingRequestStatus.UNCERTAIN,
    },
}


def validate_openrouter_scripting_request_transition(
    previous: OpenRouterScriptingRequestRecord,
    current: OpenRouterScriptingRequestRecord,
) -> None:
    if (
        previous.job_id != current.job_id
        or previous.attempt_number != current.attempt_number
        or previous.request_fingerprint != current.request_fingerprint
        or previous.fingerprint_input != current.fingerprint_input
        or previous.prepared_at != current.prepared_at
        or previous.estimated_cost_usd != current.estimated_cost_usd
        or previous.maximum_authorized_cost_usd != current.maximum_authorized_cost_usd
        or previous.requested_model != current.requested_model
    ):
        raise ValueError("OpenRouter scripting request identity changed")
    if current.status not in _ALLOWED_TRANSITIONS.get(previous.status, set()):
        raise ValueError("OpenRouter scripting request transition is invalid")


def openrouter_scripting_request_relative_path(
    record: OpenRouterScriptingRequestRecord,
) -> str:
    return (
        f"production/{record.job_id}/scripting/attempt-{record.attempt_number}/"
        "openrouter-scripting-request.json"
    )


__all__ = [
    "OpenRouterScriptingFingerprintInput",
    "OpenRouterScriptingRequestRecord",
    "OpenRouterScriptingRequestStatus",
    "OpenRouterScriptingValidationErrorCode",
    "openrouter_scripting_request_fingerprint",
    "openrouter_scripting_request_relative_path",
    "scripting_configuration_fingerprint",
    "validate_openrouter_scripting_request_transition",
]
