"""Production job aggregate contract."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProductionJob(ContractModel):
    """Durable identity and state for a prompt-to-video production."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    job_id: UUID = Field(default_factory=uuid4)
    prompt: str = Field(min_length=1)
    status: ProductionJobStatus = ProductionJobStatus.CREATED
    current_stage: ProductionStage = ProductionStage.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    configuration_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    long_form_artifact_id: UUID | None = None
    clip_project_id: UUID | None = None
    client_request_id: str | None = Field(default=None, max_length=255)
    request_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        exclude=True,
    )

    @model_validator(mode="after")
    def validate_timestamps(self) -> "ProductionJob":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self
