"""Ports and transport-neutral contracts for SCRIPTING."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.scripting.configuration import ScriptingConfiguration
from backend.src.production.scripting.models import ProductionScript

if TYPE_CHECKING:
    from backend.src.production.runtime.context import StageContext


class ReadProductionPlan(ContractModel):
    plan: ProductionPlan
    artifact_id: UUID
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    provider: str | None = None
    model_version: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="source_plan.metadata")
        if not isinstance(validated, dict):
            raise ValueError("source plan metadata must be an object")
        return validated


class ProductionPlanReader(Protocol):
    async def read_for_scripting(self, *, context: StageContext) -> ReadProductionPlan: ...


class ProductionPlanArtifactCandidate(ContractModel):
    artifact_id: UUID
    job_id: UUID
    relative_path: str
    size_bytes: int | None = None
    sha256: str | None = None
    provider: str | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ProductionPlanArtifactQueryRepository(Protocol):
    def list_candidates(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ProductionPlanArtifactCandidate, ...]: ...


class ScriptingProviderRequest(ContractModel):
    job_id: UUID
    command_id: UUID
    correlation_id: UUID
    attempt_number: int = Field(ge=1)
    source_prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_plan_artifact_id: UUID
    source_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan: ProductionPlan
    configuration: ScriptingConfiguration
    language: str = Field(min_length=2, max_length=16)
    target_duration_seconds: float = Field(gt=0, le=3600)


class ScriptingProviderResponse(ContractModel):
    script: ProductionScript
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    requested_model: str | None = Field(default=None, min_length=1, max_length=200)
    reported_model: str | None = Field(default=None, min_length=1, max_length=200)
    request_id: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=24,
        decimal_places=9,
    )
    latency_ms: float = Field(ge=0)
    finish_reason: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reported_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("reported scripting cost must not use float")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="scripting_provider.metadata")
        if not isinstance(validated, dict):
            raise ValueError("provider metadata must be an object")
        return validated


class ScriptingProvider(Protocol):
    async def generate_script(
        self,
        request: ScriptingProviderRequest,
    ) -> ScriptingProviderResponse: ...

    async def close(self) -> None: ...
