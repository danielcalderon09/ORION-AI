"""Serializable command contract for one production stage."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage


class StageCommand(ContractModel):
    """Describe a stage invocation without executing it."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    command_id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    stage: ProductionStage
    attempt_number: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    input_artifact_ids: tuple[UUID, ...] = ()
    configuration_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_artifact_ids(self) -> "StageCommand":
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("input_artifact_ids must be unique")
        return self
