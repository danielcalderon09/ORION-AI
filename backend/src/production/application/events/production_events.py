"""Pure domain event contracts for prompt-to-video production."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.results.stage_result import StageOutcome
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage


class ProductionEventType(StrEnum):
    JOB_CREATED = "production_job_created"
    JOB_QUEUED = "production_job_queued"
    STAGE_STARTED = "production_stage_started"
    STAGE_PROGRESSED = "production_stage_progressed"
    STAGE_SUCCEEDED = "production_stage_succeeded"
    STAGE_FAILED = "production_stage_failed"
    RETRY_SCHEDULED = "production_retry_scheduled"
    USER_ACTION_REQUIRED = "production_user_action_required"
    CANCELLATION_REQUESTED = "production_cancellation_requested"
    JOB_CANCELLED = "production_job_cancelled"
    JOB_COMPLETED = "production_job_completed"


class ProductionEvent(ContractModel):
    """Common envelope for events that can be persisted in a later phase."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    event_id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    event_type: ProductionEventType
    occurred_at: datetime
    sequence_number: int = Field(ge=0)
    correlation_id: UUID
    causation_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class ProductionJobCreated(ProductionEvent):
    event_type: Literal[ProductionEventType.JOB_CREATED] = ProductionEventType.JOB_CREATED


class ProductionJobQueued(ProductionEvent):
    event_type: Literal[ProductionEventType.JOB_QUEUED] = ProductionEventType.JOB_QUEUED


class ProductionStageStarted(ProductionEvent):
    event_type: Literal[ProductionEventType.STAGE_STARTED] = ProductionEventType.STAGE_STARTED
    stage: ProductionStage
    command_id: UUID
    attempt_number: int = Field(ge=1)


class ProductionStageProgressed(ProductionEvent):
    event_type: Literal[ProductionEventType.STAGE_PROGRESSED] = (
        ProductionEventType.STAGE_PROGRESSED
    )
    stage: ProductionStage
    command_id: UUID
    progress_percent: float = Field(ge=0, le=100)
    message: str | None = None


class ProductionStageSucceeded(ProductionEvent):
    event_type: Literal[ProductionEventType.STAGE_SUCCEEDED] = (
        ProductionEventType.STAGE_SUCCEEDED
    )
    stage: ProductionStage
    command_id: UUID
    output_artifact_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_artifacts(self) -> "ProductionStageSucceeded":
        if len(self.output_artifact_ids) != len(set(self.output_artifact_ids)):
            raise ValueError("output_artifact_ids must be unique")
        return self


class ProductionStageFailed(ProductionEvent):
    event_type: Literal[ProductionEventType.STAGE_FAILED] = ProductionEventType.STAGE_FAILED
    stage: ProductionStage
    command_id: UUID
    outcome: StageOutcome
    error_code: str = Field(min_length=1)
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_failure_outcome(self) -> "ProductionStageFailed":
        if self.outcome not in {
            StageOutcome.FAILED_TRANSIENT,
            StageOutcome.FAILED_PERMANENT,
        }:
            raise ValueError("ProductionStageFailed requires a failed outcome")
        return self


class ProductionRetryScheduled(ProductionEvent):
    event_type: Literal[ProductionEventType.RETRY_SCHEDULED] = (
        ProductionEventType.RETRY_SCHEDULED
    )
    stage: ProductionStage
    next_attempt_number: int = Field(ge=2)
    retry_at: datetime

    @field_validator("retry_at")
    @classmethod
    def validate_retry_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retry_at must be timezone-aware")
        return value


class ProductionUserActionRequired(ProductionEvent):
    event_type: Literal[ProductionEventType.USER_ACTION_REQUIRED] = (
        ProductionEventType.USER_ACTION_REQUIRED
    )
    stage: ProductionStage
    action_code: str = Field(min_length=1)
    instructions: str = Field(min_length=1)


class ProductionCancellationRequested(ProductionEvent):
    event_type: Literal[ProductionEventType.CANCELLATION_REQUESTED] = (
        ProductionEventType.CANCELLATION_REQUESTED
    )
    reason: str | None = None


class ProductionJobCancelled(ProductionEvent):
    event_type: Literal[ProductionEventType.JOB_CANCELLED] = (
        ProductionEventType.JOB_CANCELLED
    )
    reason: str | None = None


class ProductionJobCompleted(ProductionEvent):
    event_type: Literal[ProductionEventType.JOB_COMPLETED] = (
        ProductionEventType.JOB_COMPLETED
    )
    long_form_artifact_id: UUID | None = None
    clip_project_id: UUID | None = None


ProductionEventUnion = Annotated[
    ProductionJobCreated
    | ProductionJobQueued
    | ProductionStageStarted
    | ProductionStageProgressed
    | ProductionStageSucceeded
    | ProductionStageFailed
    | ProductionRetryScheduled
    | ProductionUserActionRequired
    | ProductionCancellationRequested
    | ProductionJobCancelled
    | ProductionJobCompleted,
    Field(discriminator="event_type"),
]
