"""Serializable result contract for one production stage."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage


class StageOutcome(StrEnum):
    """Controlled outcomes of a stage execution."""

    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"
    NEEDS_USER_ACTION = "needs_user_action"
    CANCELLED = "cancelled"


class StageResult(ContractModel):
    """Describe the completed attempt for a previously issued command."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    command_id: UUID
    job_id: UUID
    stage: ProductionStage
    outcome: StageOutcome
    started_at: datetime
    finished_at: datetime
    progress_percent: float = Field(ge=0, le=100)
    output_artifact_ids: tuple[UUID, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    retry_after_seconds: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("started_at", "finished_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stage result timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> "StageResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        if len(self.output_artifact_ids) != len(set(self.output_artifact_ids)):
            raise ValueError("output_artifact_ids must be unique")

        if self.outcome is StageOutcome.SUCCEEDED:
            if self.progress_percent != 100:
                raise ValueError("succeeded results require progress_percent=100")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("succeeded results cannot contain error details")

        failure_outcomes = {
            StageOutcome.FAILED_TRANSIENT,
            StageOutcome.FAILED_PERMANENT,
        }
        if self.outcome in failure_outcomes and not self.error_code:
            raise ValueError("failed results require error_code")

        if (
            self.outcome is not StageOutcome.FAILED_TRANSIENT
            and self.retry_after_seconds is not None
        ):
            raise ValueError("retry_after_seconds is only valid for failed_transient")

        if self.outcome is StageOutcome.CANCELLED and self.output_artifact_ids:
            raise ValueError("cancelled results cannot produce output artifacts")
        return self
