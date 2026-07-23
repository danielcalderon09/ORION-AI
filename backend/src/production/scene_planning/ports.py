"""Ports and immutable contracts for durable scene planning."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.scripting.models import ProductionScript

if TYPE_CHECKING:
    from backend.src.production.runtime.context import StageContext


class ReadProductionScript(ContractModel):
    script: ProductionScript
    artifact_id: UUID
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    provider: str | None = None
    model_version: str | None = None
    created_at: datetime | None = None


class ProductionScriptReader(Protocol):
    async def read_for_scene_planning(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionScript: ...


class ProductionScriptArtifactCandidate(ContractModel):
    artifact_id: UUID
    job_id: UUID
    relative_path: str
    size_bytes: int | None = None
    sha256: str | None = None
    provider: str | None = None
    model_version: str | None = None
    created_at: datetime


class ProductionScriptArtifactQueryRepository(Protocol):
    def list_candidates(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ProductionScriptArtifactCandidate, ...]: ...


class ScenePlanningProviderResponse(ContractModel):
    scene_plan: ProductionScenePlan
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


class ScenePlanningProvider(Protocol):
    async def generate_scene_plan(
        self,
        script: ProductionScript,
    ) -> ScenePlanningProviderResponse: ...

    async def close(self) -> None: ...
