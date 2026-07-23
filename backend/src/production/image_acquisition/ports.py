"""Ports and safe provider contracts for image acquisition."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
)
from backend.src.production.image_acquisition.models import (
    ProductionImageAcquisitionManifest,
)
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
    ProductionVisualAssetSpec,
)

if TYPE_CHECKING:
    from backend.src.production.runtime.context import StageContext


class ReadProductionVisualAssetPlan(ContractModel):
    visual_asset_plan: ProductionVisualAssetPlan
    job_id: UUID
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
        result = validate_safe_json(value, path="source_visual_asset_plan.metadata")
        if not isinstance(result, dict):
            raise ValueError("source visual asset plan metadata must be an object")
        return result


class ProductionVisualAssetPlanReader(Protocol):
    async def read_for_image_acquisition(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionVisualAssetPlan: ...


class ProductionVisualAssetPlanArtifactCandidate(ContractModel):
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


class ProductionVisualAssetPlanArtifactQueryRepository(Protocol):
    def list_candidates(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ProductionVisualAssetPlanArtifactCandidate, ...]: ...

    def list_input_artifact_types(
        self,
        *,
        job_id: UUID,
        artifact_ids: tuple[UUID, ...],
    ) -> dict[UUID, ArtifactType]: ...


class ImageAcquisitionProviderRequest(ContractModel):
    job_id: UUID
    command_id: UUID
    correlation_id: UUID
    attempt_number: int = Field(ge=1)
    visual_asset: ProductionVisualAssetSpec
    configuration: ImageAcquisitionConfiguration


class GeneratedImagePayload(ContractModel):
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    mime_type: str | None = Field(default=None, max_length=100)
    index: int = Field(ge=0, le=9)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="generated_image.metadata")
        if not isinstance(result, dict):
            raise ValueError("generated image metadata must be an object")
        return result


class ImageAcquisitionProviderResponse(ContractModel):
    images: tuple[GeneratedImagePayload, ...] = Field(min_length=1, max_length=10)
    provider: str = Field(min_length=1, max_length=100)
    requested_model: str | None = Field(default=None, max_length=300)
    reported_model: str | None = Field(default=None, max_length=300)
    request_id: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    latency_ms: float = Field(ge=0)
    finish_reason: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="image_provider.metadata")
        if not isinstance(result, dict):
            raise ValueError("image provider metadata must be an object")
        return result


class ImageAcquisitionProvider(Protocol):
    async def generate_image(
        self,
        request: ImageAcquisitionProviderRequest,
    ) -> ImageAcquisitionProviderResponse: ...

    async def close(self) -> None: ...


class ImageAcquisitionManifestWriter(Protocol):
    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> ProductionImageAcquisitionManifest | None: ...

    async def create(
        self,
        *,
        context: StageContext,
        manifest: ProductionImageAcquisitionManifest,
    ) -> None: ...

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None: ...

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: ProductionImageAcquisitionManifest,
        current: ProductionImageAcquisitionManifest,
    ) -> None: ...
