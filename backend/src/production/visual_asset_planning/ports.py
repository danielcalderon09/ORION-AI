"""Ports and immutable contracts for durable visual asset planning."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.visual_asset_planning.configuration import (
    VisualAssetPlanningConfiguration,
)
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
)

if TYPE_CHECKING:
    from backend.src.production.runtime.context import StageContext


class ReadProductionScenePlan(ContractModel):
    scene_plan: ProductionScenePlan
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
        result = validate_safe_json(value, path="source_scene_plan.metadata")
        if not isinstance(result, dict):
            raise ValueError("source scene plan metadata must be an object")
        return result


class ProductionScenePlanReader(Protocol):
    async def read_for_visual_asset_planning(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionScenePlan: ...


class ProductionScenePlanArtifactCandidate(ContractModel):
    artifact_id: UUID
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    size_bytes: int | None = None
    sha256: str | None = None
    provider: str | None = None
    model_version: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductionScenePlanArtifactQueryRepository(Protocol):
    def list_candidates(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ProductionScenePlanArtifactCandidate, ...]: ...

    def list_input_artifact_types(
        self,
        *,
        job_id: UUID,
        artifact_ids: tuple[UUID, ...],
    ) -> dict[UUID, ArtifactType]: ...


class VisualAssetPlanningProviderRequest(ContractModel):
    job_id: UUID
    command_id: UUID
    correlation_id: UUID
    attempt_number: int = Field(ge=1)
    scene_plan: ProductionScenePlan
    configuration: VisualAssetPlanningConfiguration


class VisualAssetPlanningProviderResponse(ContractModel):
    visual_asset_plan: ProductionVisualAssetPlan
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
        result = validate_safe_json(value, path="provider.metadata")
        if not isinstance(result, dict):
            raise ValueError("provider metadata must be an object")
        return result


class VisualAssetPlanningProvider(Protocol):
    async def generate_visual_asset_plan(
        self,
        request: VisualAssetPlanningProviderRequest,
    ) -> VisualAssetPlanningProviderResponse: ...

    async def close(self) -> None: ...
