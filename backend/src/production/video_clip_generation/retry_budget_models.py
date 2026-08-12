"""Provider-neutral durable authorization for bounded video retry exposure."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage


class VideoRetryBudgetAuthorization(ContractModel):
    """One job-scoped overlay over an immutable aggregate visual budget."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    stage: Literal[ProductionStage.GENERATING_VIDEO_CLIPS] = ProductionStage.GENERATING_VIDEO_CLIPS
    source_stage_attempt: int = Field(ge=1)
    target_stage_attempt: int = Field(ge=2)
    original_aggregate_visual_budget_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_video_generation_manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_accounted_video_cost_usd: Decimal = Field(ge=0, decimal_places=9)
    current_accounted_image_cost_usd: Decimal = Field(ge=0, decimal_places=9)
    original_authorized_video_job_cost_usd: Decimal = Field(ge=0, decimal_places=9)
    new_authorized_video_job_cost_usd: Decimal = Field(gt=0, decimal_places=9)
    ceiling_increase_usd: Decimal = Field(gt=0, decimal_places=9)
    maximum_additional_provider_requests: int = Field(ge=1, le=100)
    maximum_additional_estimated_provider_cost_usd: Decimal = Field(gt=0, decimal_places=9)
    provider_requests_already_consumed: int = Field(ge=0, le=100_000)
    maximum_total_provider_requests: int = Field(ge=1, le=100_000)
    maximum_authorized_cost_per_request_usd: Decimal = Field(gt=0, decimal_places=9)
    durable_total_visual_cost_ceiling_usd: Decimal = Field(gt=0, decimal_places=9)
    projected_worst_case_visual_cost_usd: Decimal = Field(gt=0, decimal_places=9)
    settings_video_job_ceiling_at_authorization_usd: Decimal = Field(gt=0, decimal_places=9)
    authorized_at: datetime
    operator_id: str = Field(min_length=1, max_length=100)
    reason: Literal["operator_authorized_retryable_video_recovery"] = (
        "operator_authorized_retryable_video_recovery"
    )
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def create(cls, **values: Any) -> VideoRetryBudgetAuthorization:
        candidate = cls.model_construct(fingerprint="0" * 64, **values)
        payload = candidate.model_copy(
            update={"fingerprint": video_retry_budget_authorization_fingerprint(candidate)}
        ).model_dump(mode="python")
        return cls.model_validate(payload)

    @field_validator(
        "current_accounted_video_cost_usd",
        "current_accounted_image_cost_usd",
        "original_authorized_video_job_cost_usd",
        "new_authorized_video_job_cost_usd",
        "ceiling_increase_usd",
        "maximum_additional_estimated_provider_cost_usd",
        "maximum_authorized_cost_per_request_usd",
        "durable_total_visual_cost_ceiling_usd",
        "projected_worst_case_visual_cost_usd",
        "settings_video_job_ceiling_at_authorization_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("video retry authorization money must use Decimal text")
        return value

    @field_validator("authorized_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("video retry authorization time must be timezone-aware")
        return value

    @field_validator("operator_id")
    @classmethod
    def safe_operator_id(cls, value: str) -> str:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:"
        if any(character not in allowed for character in value):
            raise ValueError("video retry authorization operator identity is unsafe")
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> VideoRetryBudgetAuthorization:
        if self.target_stage_attempt != self.source_stage_attempt + 1:
            raise ValueError("video retry authorization must target the next attempt")
        if self.new_authorized_video_job_cost_usd <= self.original_authorized_video_job_cost_usd:
            raise ValueError("video retry authorization must increase the video ceiling")
        if self.ceiling_increase_usd != (
            self.new_authorized_video_job_cost_usd - self.original_authorized_video_job_cost_usd
        ):
            raise ValueError("video retry ceiling increase is inconsistent")
        if self.new_authorized_video_job_cost_usd != (
            self.current_accounted_video_cost_usd
            + self.maximum_additional_estimated_provider_cost_usd
        ):
            raise ValueError("video retry ceiling is not the minimum required exposure")
        if (
            self.provider_requests_already_consumed + (self.maximum_additional_provider_requests)
            > self.maximum_total_provider_requests
        ):
            raise ValueError("video retry request capacity exceeds the durable limit")
        if self.maximum_additional_estimated_provider_cost_usd > (
            self.maximum_additional_provider_requests * self.maximum_authorized_cost_per_request_usd
        ):
            raise ValueError("video retry additional cost exceeds request capacity")
        if self.projected_worst_case_visual_cost_usd != (
            self.current_accounted_image_cost_usd + self.new_authorized_video_job_cost_usd
        ):
            raise ValueError("video retry projected visual cost is inconsistent")
        if self.projected_worst_case_visual_cost_usd > self.durable_total_visual_cost_ceiling_usd:
            raise ValueError("video retry would exceed the durable total visual ceiling")
        if (
            self.new_authorized_video_job_cost_usd
            > self.settings_video_job_ceiling_at_authorization_usd
        ):
            raise ValueError("video retry exceeds the current global Settings ceiling")
        if self.fingerprint != video_retry_budget_authorization_fingerprint(self):
            raise ValueError("video retry authorization fingerprint differs")
        return self


def video_retry_budget_authorization_fingerprint(
    value: VideoRetryBudgetAuthorization,
) -> str:
    payload = value.model_dump(mode="json", exclude={"fingerprint"})
    return hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def serialize_video_retry_budget_authorization(
    value: VideoRetryBudgetAuthorization,
) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def deserialize_video_retry_budget_authorization(
    content: bytes,
) -> VideoRetryBudgetAuthorization:
    return VideoRetryBudgetAuthorization.model_validate(
        json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
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
    "VideoRetryBudgetAuthorization",
    "deserialize_video_retry_budget_authorization",
    "serialize_video_retry_budget_authorization",
    "video_retry_budget_authorization_fingerprint",
]
