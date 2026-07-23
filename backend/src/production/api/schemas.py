"""Explicit public HTTP contracts for Production Jobs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.src.production.application.events import ProductionEventUnion
from backend.src.production.application.sanitization import (
    UnsafeProductionDataError,
    sanitize_public_json,
    validate_safe_json,
)
from backend.src.production.application.services.models import (
    ProductionArtifactView,
    ProductionJobView,
)
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionJobStatus,
    ProductionStage,
)
from backend.src.production.planning.models import PlanningJobConfiguration
from backend.src.production.scripting.configuration import ScriptingConfiguration
from backend.src.production.visual_asset_planning.configuration import (
    VisualAssetPlanningConfiguration,
)


class PublicSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProductionJobRequest(PublicSchema):
    prompt: str = Field(min_length=1, max_length=10_000)
    configuration: dict[str, Any] = Field(default_factory=dict)
    generate_clips_after_render: bool = False
    client_request_id: str | None = Field(default=None, min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("prompt must not be empty")
        return normalized

    @field_validator("configuration", "metadata")
    @classmethod
    def validate_json(cls, value: dict[str, Any], info: Any) -> dict[str, Any]:
        try:
            validated = validate_safe_json(value, path=info.field_name)
        except UnsafeProductionDataError as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(validated, dict):
            raise ValueError(f"{info.field_name} must be an object")
        if info.field_name == "configuration" and (
            "planning" in validated
            or "scripting" in validated
            or "visual_asset_planning" in validated
            or "image_acquisition" in validated
        ):
            unknown = set(validated) - {
                "planning",
                "scripting",
                "visual_asset_planning",
            }
            if unknown:
                raise ValueError("nested configuration contains unsupported capabilities")
            PlanningJobConfiguration.model_validate(validated.get("planning", {}))
            ScriptingConfiguration.model_validate(validated.get("scripting", {}))
            VisualAssetPlanningConfiguration.model_validate(
                validated.get("visual_asset_planning", {})
            )
        return validated


class ProductionJobResponse(PublicSchema):
    job_id: UUID
    prompt: str
    status: ProductionJobStatus
    current_stage: ProductionStage
    progress_percent: float = Field(ge=0, le=100)
    error_code: str | None
    error_message: str | None
    cancellation_requested: bool
    configuration: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    row_version: int = Field(ge=1)

    @classmethod
    def from_view(cls, view: ProductionJobView) -> "ProductionJobResponse":
        job = view.job
        stages = list(ProductionStage)
        progress = 100.0 if job.status is ProductionJobStatus.COMPLETED else (
            stages.index(job.current_stage) / (len(stages) - 1) * 100
        )
        configuration = job.configuration_snapshot.get("configuration", {})
        return cls(
            job_id=job.job_id,
            prompt=job.prompt,
            status=job.status,
            current_stage=job.current_stage,
            progress_percent=round(progress, 2),
            error_code=job.error_code,
            error_message=job.error_message,
            cancellation_requested=job.status in {
                ProductionJobStatus.CANCEL_REQUESTED,
                ProductionJobStatus.CANCELLED,
            },
            configuration=sanitize_public_json(configuration),
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=view.completed_at,
            row_version=view.row_version,
        )


class ProductionJobListResponse(PublicSchema):
    items: tuple[ProductionJobResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ProductionEventResponse(PublicSchema):
    event_id: UUID
    event_type: str
    sequence_number: int
    occurred_at: datetime
    stage: ProductionStage | None = None
    correlation_id: UUID
    causation_id: UUID | None
    metadata: dict[str, Any]

    @classmethod
    def from_event(cls, event: ProductionEventUnion) -> "ProductionEventResponse":
        return cls(
            event_id=event.event_id,
            event_type=event.event_type.value,
            sequence_number=event.sequence_number,
            occurred_at=event.occurred_at,
            stage=getattr(event, "stage", None),
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            metadata=sanitize_public_json(event.metadata),
        )


class ProductionEventListResponse(PublicSchema):
    items: tuple[ProductionEventResponse, ...]


class ProductionArtifactResponse(PublicSchema):
    artifact_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    mime_type: str
    status: ArtifactStatus
    size_bytes: int | None
    checksum: str | None
    provider: str | None
    model_version: str | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: ProductionArtifactView) -> "ProductionArtifactResponse":
        artifact = view.artifact
        return cls(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            relative_path=artifact.relative_path,
            mime_type=artifact.mime_type,
            status=artifact.status,
            size_bytes=artifact.size_bytes,
            checksum=artifact.sha256,
            provider=artifact.provider,
            model_version=artifact.model_version,
            metadata=sanitize_public_json(artifact.metadata),
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class ProductionArtifactListResponse(PublicSchema):
    items: tuple[ProductionArtifactResponse, ...]


class ProductionOperationResponse(PublicSchema):
    operation: str
    idempotent: bool
    job: ProductionJobResponse
