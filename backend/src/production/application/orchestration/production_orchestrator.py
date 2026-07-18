"""Deterministic decision coordinator for the production pipeline."""

import hashlib
import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from backend.src.production.application.commands.stage_command import StageCommand
from backend.src.production.application.events.production_events import (
    ProductionEventUnion,
    ProductionJobCancelled,
    ProductionJobCompleted,
    ProductionJobQueued,
    ProductionRetryScheduled,
    ProductionStageFailed,
    ProductionStageStarted,
    ProductionStageSucceeded,
    ProductionUserActionRequired,
)
from backend.src.production.application.orchestration.stage_registry import StageRegistry
from backend.src.production.application.orchestration.transition_policy import TransitionPolicy
from backend.src.production.application.results.stage_result import StageOutcome, StageResult
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob

Clock = Callable[[], datetime]
UUIDFactory = Callable[[], UUID]


class StageResultMismatchError(ValueError):
    """Raised when a result does not correspond to its expected command."""


class DuplicateStageResultError(ValueError):
    """Raised when the same command result is processed twice in a decision."""


class PipelineConfiguration(ContractModel):
    """Minimal deterministic configuration used to choose pipeline stages."""

    generate_clips_after_render: bool = False
    default_retry_after_seconds: float = Field(default=60, gt=0)
    input_artifact_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_artifacts(self) -> "PipelineConfiguration":
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("input_artifact_ids must be unique")
        return self


class OrchestrationDecision(ContractModel):
    """Complete, side-effect-free result of one orchestration decision."""

    updated_job: ProductionJob
    next_command: StageCommand | None = None
    events: tuple[ProductionEventUnion, ...] = ()
    should_continue: bool
    reason: str | None = None


class IdempotencyKeyFactory:
    """Build stable stage keys from normalized command inputs."""

    @classmethod
    def create(
        cls,
        *,
        job_id: UUID,
        stage: ProductionStage,
        attempt_number: int,
        input_artifact_ids: Collection[UUID],
        configuration_snapshot: Mapping[str, Any],
    ) -> str:
        payload = {
            "job_id": str(job_id),
            "stage": stage.value,
            "attempt_number": attempt_number,
            "input_artifact_ids": sorted(str(item) for item in input_artifact_ids),
            "configuration_snapshot": cls._normalize(configuration_snapshot),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"production-stage-v1:{hashlib.sha256(encoded).hexdigest()}"

    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): cls._normalize(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [cls._normalize(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(cls._normalize(item) for item in value)
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        raise TypeError(f"configuration value is not JSON-compatible: {type(value).__name__}")


def validate_stage_result(
    command: StageCommand,
    result: StageResult,
    *,
    processed_command_ids: Collection[UUID] = (),
) -> None:
    """Validate result identity without reading or updating durable state."""

    if result.command_id in processed_command_ids:
        raise DuplicateStageResultError(f"result already processed for command {result.command_id}")
    if result.command_id != command.command_id:
        raise StageResultMismatchError("StageResult command_id does not match StageCommand")
    if result.job_id != command.job_id:
        raise StageResultMismatchError("StageResult job_id does not match StageCommand")
    if result.stage is not command.stage:
        raise StageResultMismatchError("StageResult stage does not match StageCommand")


@dataclass(slots=True)
class _EventEnvelopeFactory:
    uuid_factory: UUIDFactory
    job_id: UUID
    occurred_at: datetime
    correlation_id: UUID
    next_sequence_number: int

    def next(self, *, causation_id: UUID | None = None) -> dict[str, Any]:
        envelope = {
            "event_id": self.uuid_factory(),
            "job_id": self.job_id,
            "occurred_at": self.occurred_at,
            "sequence_number": self.next_sequence_number,
            "correlation_id": self.correlation_id,
            "causation_id": causation_id,
        }
        self.next_sequence_number += 1
        return envelope


class ProductionOrchestrator:
    """Choose state transitions, commands, and events without executing IO."""

    def __init__(self, *, clock: Clock, uuid_factory: UUIDFactory) -> None:
        self._clock = clock
        self._uuid_factory = uuid_factory

    def decide(
        self,
        job: ProductionJob,
        configuration: PipelineConfiguration,
        *,
        last_command: StageCommand | None = None,
        last_result: StageResult | None = None,
        next_sequence_number: int = 0,
        next_attempt_number: int = 1,
        correlation_id: UUID | None = None,
        processed_result_command_ids: Collection[UUID] = (),
    ) -> OrchestrationDecision:
        if next_sequence_number < 0:
            raise ValueError("next_sequence_number must be non-negative")
        if next_attempt_number < 1:
            raise ValueError("next_attempt_number must be positive")
        if last_result is not None and last_command is None:
            raise StageResultMismatchError("last_result requires its expected last_command")

        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("orchestrator clock must return a timezone-aware datetime")

        envelopes = _EventEnvelopeFactory(
            uuid_factory=self._uuid_factory,
            job_id=job.job_id,
            occurred_at=now,
            correlation_id=correlation_id or job.job_id,
            next_sequence_number=next_sequence_number,
        )

        if job.status is ProductionJobStatus.CANCEL_REQUESTED:
            return self._cancel(job, now=now, envelopes=envelopes)

        if job.status in {
            ProductionJobStatus.CANCELLED,
            ProductionJobStatus.COMPLETED,
            ProductionJobStatus.FAILED,
        }:
            return OrchestrationDecision(
                updated_job=job,
                should_continue=False,
                reason=f"terminal_status:{job.status.value}",
            )

        if job.status is ProductionJobStatus.CREATED:
            TransitionPolicy.validate_transition(job.status, ProductionJobStatus.QUEUED)
            updated_job = job.model_copy(
                update={"status": ProductionJobStatus.QUEUED, "updated_at": now}
            )
            event = ProductionJobQueued(**envelopes.next())
            return OrchestrationDecision(
                updated_job=updated_job,
                events=(event,),
                should_continue=True,
                reason="job_queued",
            )

        if job.status is ProductionJobStatus.QUEUED:
            return self._start_queued_job(
                job,
                configuration,
                now=now,
                envelopes=envelopes,
                attempt_number=next_attempt_number,
            )

        if job.status is ProductionJobStatus.RUNNING:
            if last_command is None or last_result is None:
                return OrchestrationDecision(
                    updated_job=job,
                    should_continue=False,
                    reason="awaiting_stage_result",
                )
            validate_stage_result(
                last_command,
                last_result,
                processed_command_ids=processed_result_command_ids,
            )
            if last_command.job_id != job.job_id or last_command.stage is not job.current_stage:
                raise StageResultMismatchError(
                    "last command does not match the current production job"
                )
            return self._apply_result(
                job,
                configuration,
                command=last_command,
                result=last_result,
                now=now,
                envelopes=envelopes,
            )

        return OrchestrationDecision(
            updated_job=job,
            should_continue=False,
            reason=f"awaiting_external_transition:{job.status.value}",
        )

    def _start_queued_job(
        self,
        job: ProductionJob,
        configuration: PipelineConfiguration,
        *,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
        attempt_number: int,
    ) -> OrchestrationDecision:
        TransitionPolicy.validate_transition(job.status, ProductionJobStatus.RUNNING)
        if job.current_stage is ProductionStage.CREATED:
            stage = StageRegistry.next_stage(
                job.current_stage,
                generate_clips_after_render=configuration.generate_clips_after_render,
            )
            if stage is None:
                raise ValueError("created stage must have a following pipeline stage")
        else:
            stage = job.current_stage

        command = self._create_command(
            job,
            configuration,
            stage=stage,
            attempt_number=attempt_number,
            input_artifact_ids=configuration.input_artifact_ids,
            now=now,
        )
        updated_job = job.model_copy(
            update={
                "status": ProductionJobStatus.RUNNING,
                "current_stage": stage,
                "updated_at": now,
                "error_code": None,
                "error_message": None,
            }
        )
        event = ProductionStageStarted(
            **envelopes.next(causation_id=command.command_id),
            stage=stage,
            command_id=command.command_id,
            attempt_number=command.attempt_number,
        )
        return OrchestrationDecision(
            updated_job=updated_job,
            next_command=command,
            events=(event,),
            should_continue=True,
            reason="stage_command_created",
        )

    def _apply_result(
        self,
        job: ProductionJob,
        configuration: PipelineConfiguration,
        *,
        command: StageCommand,
        result: StageResult,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
    ) -> OrchestrationDecision:
        if result.outcome is StageOutcome.SUCCEEDED:
            return self._apply_success(
                job,
                configuration,
                command=command,
                result=result,
                now=now,
                envelopes=envelopes,
            )
        if result.outcome is StageOutcome.FAILED_TRANSIENT:
            return self._apply_transient_failure(
                job,
                configuration,
                command=command,
                result=result,
                now=now,
                envelopes=envelopes,
            )
        if result.outcome is StageOutcome.FAILED_PERMANENT:
            return self._apply_permanent_failure(
                job,
                command=command,
                result=result,
                now=now,
                envelopes=envelopes,
            )
        if result.outcome is StageOutcome.NEEDS_USER_ACTION:
            return self._apply_user_action(
                job,
                command=command,
                result=result,
                now=now,
                envelopes=envelopes,
            )
        return self._cancel(
            job,
            now=now,
            envelopes=envelopes,
            causation_id=command.command_id,
            from_running_result=True,
        )

    def _apply_success(
        self,
        job: ProductionJob,
        configuration: PipelineConfiguration,
        *,
        command: StageCommand,
        result: StageResult,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
    ) -> OrchestrationDecision:
        succeeded = ProductionStageSucceeded(
            **envelopes.next(causation_id=command.command_id),
            stage=command.stage,
            command_id=command.command_id,
            output_artifact_ids=result.output_artifact_ids,
        )
        next_stage = StageRegistry.next_stage(
            command.stage,
            generate_clips_after_render=configuration.generate_clips_after_render,
        )
        if next_stage is None or next_stage is ProductionStage.COMPLETED:
            TransitionPolicy.validate_transition(job.status, ProductionJobStatus.COMPLETED)
            updated_job = job.model_copy(
                update={
                    "status": ProductionJobStatus.COMPLETED,
                    "current_stage": ProductionStage.COMPLETED,
                    "updated_at": now,
                    "error_code": None,
                    "error_message": None,
                }
            )
            completed = ProductionJobCompleted(
                **envelopes.next(causation_id=succeeded.event_id),
                long_form_artifact_id=updated_job.long_form_artifact_id,
                clip_project_id=updated_job.clip_project_id,
            )
            return OrchestrationDecision(
                updated_job=updated_job,
                events=(succeeded, completed),
                should_continue=False,
                reason="pipeline_completed",
            )

        next_command = self._create_command(
            job,
            configuration,
            stage=next_stage,
            attempt_number=1,
            input_artifact_ids=result.output_artifact_ids,
            now=now,
        )
        updated_job = job.model_copy(
            update={
                "current_stage": next_stage,
                "updated_at": now,
                "error_code": None,
                "error_message": None,
            }
        )
        started = ProductionStageStarted(
            **envelopes.next(causation_id=next_command.command_id),
            stage=next_stage,
            command_id=next_command.command_id,
            attempt_number=1,
        )
        return OrchestrationDecision(
            updated_job=updated_job,
            next_command=next_command,
            events=(succeeded, started),
            should_continue=True,
            reason="advanced_to_next_stage",
        )

    def _apply_transient_failure(
        self,
        job: ProductionJob,
        configuration: PipelineConfiguration,
        *,
        command: StageCommand,
        result: StageResult,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
    ) -> OrchestrationDecision:
        TransitionPolicy.validate_transition(job.status, ProductionJobStatus.WAITING_FOR_RETRY)
        updated_job = job.model_copy(
            update={
                "status": ProductionJobStatus.WAITING_FOR_RETRY,
                "updated_at": now,
                "error_code": result.error_code,
                "error_message": result.error_message,
            }
        )
        failed = ProductionStageFailed(
            **envelopes.next(causation_id=command.command_id),
            stage=command.stage,
            command_id=command.command_id,
            outcome=result.outcome,
            error_code=result.error_code or "transient_failure",
            error_message=result.error_message,
        )
        retry_delay = result.retry_after_seconds or configuration.default_retry_after_seconds
        retry = ProductionRetryScheduled(
            **envelopes.next(causation_id=failed.event_id),
            stage=command.stage,
            next_attempt_number=command.attempt_number + 1,
            retry_at=now + timedelta(seconds=retry_delay),
        )
        return OrchestrationDecision(
            updated_job=updated_job,
            events=(failed, retry),
            should_continue=False,
            reason="retry_scheduled",
        )

    def _apply_permanent_failure(
        self,
        job: ProductionJob,
        *,
        command: StageCommand,
        result: StageResult,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
    ) -> OrchestrationDecision:
        TransitionPolicy.validate_transition(job.status, ProductionJobStatus.FAILED)
        updated_job = job.model_copy(
            update={
                "status": ProductionJobStatus.FAILED,
                "updated_at": now,
                "error_code": result.error_code,
                "error_message": result.error_message,
            }
        )
        event = ProductionStageFailed(
            **envelopes.next(causation_id=command.command_id),
            stage=command.stage,
            command_id=command.command_id,
            outcome=result.outcome,
            error_code=result.error_code or "permanent_failure",
            error_message=result.error_message,
        )
        return OrchestrationDecision(
            updated_job=updated_job,
            events=(event,),
            should_continue=False,
            reason="permanent_failure",
        )

    def _apply_user_action(
        self,
        job: ProductionJob,
        *,
        command: StageCommand,
        result: StageResult,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
    ) -> OrchestrationDecision:
        TransitionPolicy.validate_transition(job.status, ProductionJobStatus.NEEDS_USER_ACTION)
        action_code = result.error_code or "user_action_required"
        instructions = result.error_message or "User action is required before retrying this stage."
        updated_job = job.model_copy(
            update={
                "status": ProductionJobStatus.NEEDS_USER_ACTION,
                "updated_at": now,
                "error_code": action_code,
                "error_message": instructions,
            }
        )
        event = ProductionUserActionRequired(
            **envelopes.next(causation_id=command.command_id),
            stage=command.stage,
            action_code=action_code,
            instructions=instructions,
        )
        return OrchestrationDecision(
            updated_job=updated_job,
            events=(event,),
            should_continue=False,
            reason="user_action_required",
        )

    def _cancel(
        self,
        job: ProductionJob,
        *,
        now: datetime,
        envelopes: _EventEnvelopeFactory,
        causation_id: UUID | None = None,
        from_running_result: bool = False,
    ) -> OrchestrationDecision:
        if from_running_result:
            TransitionPolicy.validate_transition(
                ProductionJobStatus.RUNNING,
                ProductionJobStatus.CANCEL_REQUESTED,
            )
            TransitionPolicy.validate_transition(
                ProductionJobStatus.CANCEL_REQUESTED,
                ProductionJobStatus.CANCELLED,
            )
        else:
            TransitionPolicy.validate_transition(job.status, ProductionJobStatus.CANCELLED)
        updated_job = job.model_copy(
            update={"status": ProductionJobStatus.CANCELLED, "updated_at": now}
        )
        event = ProductionJobCancelled(
            **envelopes.next(causation_id=causation_id),
            reason="stage_cancelled" if from_running_result else "cancellation_requested",
        )
        return OrchestrationDecision(
            updated_job=updated_job,
            events=(event,),
            should_continue=False,
            reason="job_cancelled",
        )

    def _create_command(
        self,
        job: ProductionJob,
        configuration: PipelineConfiguration,
        *,
        stage: ProductionStage,
        attempt_number: int,
        input_artifact_ids: Collection[UUID],
        now: datetime,
    ) -> StageCommand:
        if stage in {ProductionStage.CREATED, ProductionStage.COMPLETED}:
            raise ValueError(f"cannot create a command for stage {stage.value}")
        snapshot = dict(job.configuration_snapshot)
        snapshot["generate_clips_after_render"] = configuration.generate_clips_after_render
        idempotency_key = IdempotencyKeyFactory.create(
            job_id=job.job_id,
            stage=stage,
            attempt_number=attempt_number,
            input_artifact_ids=input_artifact_ids,
            configuration_snapshot=snapshot,
        )
        return StageCommand(
            command_id=self._uuid_factory(),
            job_id=job.job_id,
            stage=stage,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key,
            input_artifact_ids=tuple(input_artifact_ids),
            configuration_snapshot=snapshot,
            created_at=now,
        )
