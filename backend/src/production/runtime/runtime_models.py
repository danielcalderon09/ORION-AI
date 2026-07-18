"""Strict runtime contracts for local production execution."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.results.stage_result import StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage


class ProductionLease(ContractModel):
    job_id: UUID
    owner_id: str = Field(min_length=1)
    lease_until: datetime
    heartbeat_at: datetime
    version: int = Field(ge=1)

    @field_validator("lease_until", "heartbeat_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lease timestamps must be timezone-aware")
        return value


class StageExecutionOutput(ContractModel):
    result: StageResult
    artifacts: tuple[Artifact, ...] = ()


class WorkerRunResult(ContractModel):
    processed: bool
    job_id: UUID | None = None
    previous_status: ProductionJobStatus | None = None
    updated_status: ProductionJobStatus | None = None
    updated_stage: ProductionStage | None = None
    command_id: UUID | None = None
    reason: str | None = None


class RuntimeRecoveryResult(ContractModel):
    requeued_job_ids: tuple[UUID, ...] = ()
    expired_lease_job_ids: tuple[UUID, ...] = ()
