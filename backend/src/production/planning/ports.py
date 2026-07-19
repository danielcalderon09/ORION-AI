"""Ports and transport-neutral request/response contracts for planning."""

from typing import Any, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.models import (
    PlanningJobConfiguration,
    ProductionPlan,
    SupportedAspectRatio,
)


class PlanningProviderRequest(ContractModel):
    job_id: UUID
    prompt: str = Field(min_length=1, max_length=10_000)
    configuration: PlanningJobConfiguration
    target_duration_seconds: float = Field(gt=0, le=3600)
    language: str = Field(min_length=2, max_length=16)
    aspect_ratio: SupportedAspectRatio
    correlation_id: UUID
    attempt_number: int = Field(ge=1)


class PlanningProviderResponse(ContractModel):
    plan: ProductionPlan
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    requested_model: str | None = Field(default=None, min_length=1, max_length=200)
    reported_model: str | None = Field(default=None, min_length=1, max_length=200)
    request_id: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    finish_reason: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="provider.metadata")
        if not isinstance(validated, dict):
            raise ValueError("provider metadata must be an object")
        return validated


class PlanningProvider(Protocol):
    async def generate_plan(
        self,
        request: PlanningProviderRequest,
    ) -> PlanningProviderResponse: ...

    async def close(self) -> None: ...
