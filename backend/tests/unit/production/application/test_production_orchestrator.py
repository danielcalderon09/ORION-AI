"""Deterministic unit tests for ProductionOrchestrator decisions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.events import (
    ProductionJobCancelled,
    ProductionJobCompleted,
    ProductionJobQueued,
    ProductionRetryScheduled,
    ProductionStageFailed,
    ProductionStageStarted,
    ProductionStageSucceeded,
    ProductionUserActionRequired,
)
from backend.src.production.application.orchestration import (
    DuplicateStageResultError,
    PipelineConfiguration,
    ProductionOrchestrator,
    StageResultMismatchError,
    validate_stage_result,
)
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000001")
ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000001")


class UUIDSequence:
    """Repeatable UUID factory with a private local counter."""

    def __init__(self) -> None:
        self._next_value = 1

    def __call__(self) -> UUID:
        value = UUID(f"90000000-0000-4000-8000-{self._next_value:012d}")
        self._next_value += 1
        return value


def make_orchestrator() -> ProductionOrchestrator:
    return ProductionOrchestrator(clock=lambda: NOW, uuid_factory=UUIDSequence())


def make_job(
    *,
    status: ProductionJobStatus,
    stage: ProductionStage,
) -> ProductionJob:
    return ProductionJob(
        job_id=JOB_ID,
        prompt="Create a deterministic video",
        status=status,
        current_stage=stage,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
        configuration_snapshot={"language": "es"},
    )


def make_command(
    *,
    command_id: UUID = COMMAND_ID,
    job_id: UUID = JOB_ID,
    stage: ProductionStage = ProductionStage.PLANNING,
    attempt_number: int = 1,
) -> StageCommand:
    return StageCommand(
        command_id=command_id,
        job_id=job_id,
        stage=stage,
        attempt_number=attempt_number,
        idempotency_key="production-stage-v1:expected",
        created_at=NOW - timedelta(seconds=5),
    )


def make_result(
    command: StageCommand,
    *,
    outcome: StageOutcome = StageOutcome.SUCCEEDED,
    command_id: UUID | None = None,
    job_id: UUID | None = None,
    stage: ProductionStage | None = None,
) -> StageResult:
    error_code = None
    error_message = None
    retry_after_seconds = None
    output_artifact_ids: tuple[UUID, ...] = (ARTIFACT_ID,)
    progress = 100
    if outcome in {StageOutcome.FAILED_TRANSIENT, StageOutcome.FAILED_PERMANENT}:
        error_code = "stage_failure"
        error_message = "The stage failed"
        output_artifact_ids = ()
        progress = 40
    if outcome is StageOutcome.FAILED_TRANSIENT:
        retry_after_seconds = 30
    if outcome is StageOutcome.NEEDS_USER_ACTION:
        error_code = "confirm_plan"
        error_message = "Confirm the proposed plan"
        output_artifact_ids = ()
        progress = 60
    if outcome is StageOutcome.CANCELLED:
        output_artifact_ids = ()
        progress = 40
    return StageResult(
        command_id=command_id or command.command_id,
        job_id=job_id or command.job_id,
        stage=stage or command.stage,
        outcome=outcome,
        started_at=NOW - timedelta(seconds=4),
        finished_at=NOW - timedelta(seconds=1),
        progress_percent=progress,
        output_artifact_ids=output_artifact_ids,
        error_code=error_code,
        error_message=error_message,
        retry_after_seconds=retry_after_seconds,
    )


def test_created_job_produces_queued_decision_without_command() -> None:
    original = make_job(status=ProductionJobStatus.CREATED, stage=ProductionStage.CREATED)

    decision = make_orchestrator().decide(original, PipelineConfiguration())

    assert original.status is ProductionJobStatus.CREATED
    assert decision.updated_job.status is ProductionJobStatus.QUEUED
    assert decision.next_command is None
    assert decision.should_continue
    assert len(decision.events) == 1
    assert isinstance(decision.events[0], ProductionJobQueued)


def test_queued_job_generates_first_stage_command() -> None:
    job = make_job(status=ProductionJobStatus.QUEUED, stage=ProductionStage.CREATED)

    decision = make_orchestrator().decide(job, PipelineConfiguration())

    assert decision.updated_job.status is ProductionJobStatus.RUNNING
    assert decision.updated_job.current_stage is ProductionStage.PLANNING
    assert decision.next_command is not None
    assert decision.next_command.stage is ProductionStage.PLANNING
    assert decision.next_command.attempt_number == 1
    assert decision.next_command.idempotency_key.startswith("production-stage-v1:")
    assert isinstance(decision.events[0], ProductionStageStarted)


def test_succeeded_result_advances_to_next_stage() -> None:
    job = make_job(status=ProductionJobStatus.RUNNING, stage=ProductionStage.PLANNING)
    command = make_command()

    decision = make_orchestrator().decide(
        job,
        PipelineConfiguration(),
        last_command=command,
        last_result=make_result(command),
    )

    assert decision.updated_job.current_stage is ProductionStage.SCRIPTING
    assert decision.next_command is not None
    assert decision.next_command.stage is ProductionStage.SCRIPTING
    assert isinstance(decision.events[0], ProductionStageSucceeded)
    assert isinstance(decision.events[1], ProductionStageStarted)


def test_transient_failure_schedules_retry_without_command() -> None:
    job = make_job(status=ProductionJobStatus.RUNNING, stage=ProductionStage.PLANNING)
    command = make_command(attempt_number=2)

    decision = make_orchestrator().decide(
        job,
        PipelineConfiguration(),
        last_command=command,
        last_result=make_result(command, outcome=StageOutcome.FAILED_TRANSIENT),
    )

    assert decision.updated_job.status is ProductionJobStatus.WAITING_FOR_RETRY
    assert decision.next_command is None
    assert not decision.should_continue
    assert isinstance(decision.events[0], ProductionStageFailed)
    retry = decision.events[1]
    assert isinstance(retry, ProductionRetryScheduled)
    assert retry.next_attempt_number == 3
    assert retry.retry_at == NOW + timedelta(seconds=30)


def test_permanent_failure_finishes_in_failed() -> None:
    job = make_job(status=ProductionJobStatus.RUNNING, stage=ProductionStage.PLANNING)
    command = make_command()

    decision = make_orchestrator().decide(
        job,
        PipelineConfiguration(),
        last_command=command,
        last_result=make_result(command, outcome=StageOutcome.FAILED_PERMANENT),
    )

    assert decision.updated_job.status is ProductionJobStatus.FAILED
    assert decision.next_command is None
    assert isinstance(decision.events[0], ProductionStageFailed)


def test_user_action_result_finishes_in_needs_user_action() -> None:
    job = make_job(status=ProductionJobStatus.RUNNING, stage=ProductionStage.PLANNING)
    command = make_command()

    decision = make_orchestrator().decide(
        job,
        PipelineConfiguration(),
        last_command=command,
        last_result=make_result(command, outcome=StageOutcome.NEEDS_USER_ACTION),
    )

    assert decision.updated_job.status is ProductionJobStatus.NEEDS_USER_ACTION
    event = decision.events[0]
    assert isinstance(event, ProductionUserActionRequired)
    assert event.action_code == "confirm_plan"


def test_cancel_requested_creates_no_new_command() -> None:
    job = make_job(
        status=ProductionJobStatus.CANCEL_REQUESTED,
        stage=ProductionStage.PLANNING,
    )

    decision = make_orchestrator().decide(job, PipelineConfiguration())

    assert decision.updated_job.status is ProductionJobStatus.CANCELLED
    assert decision.next_command is None
    assert not decision.should_continue
    assert isinstance(decision.events[0], ProductionJobCancelled)


def test_last_stage_success_completes_job() -> None:
    job = make_job(
        status=ProductionJobStatus.RUNNING,
        stage=ProductionStage.VALIDATING_RENDER,
    )
    command = make_command(stage=ProductionStage.VALIDATING_RENDER)

    decision = make_orchestrator().decide(
        job,
        PipelineConfiguration(generate_clips_after_render=False),
        last_command=command,
        last_result=make_result(command),
    )

    assert decision.updated_job.status is ProductionJobStatus.COMPLETED
    assert decision.updated_job.current_stage is ProductionStage.COMPLETED
    assert decision.next_command is None
    assert isinstance(decision.events[0], ProductionStageSucceeded)
    assert isinstance(decision.events[1], ProductionJobCompleted)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("command_id", UUID("20000000-0000-4000-8000-000000000099"), "command_id"),
        ("job_id", UUID("10000000-0000-4000-8000-000000000099"), "job_id"),
        ("stage", ProductionStage.SCRIPTING, "stage"),
    ],
)
def test_stage_result_identity_mismatch_is_rejected(field: str, value, message: str) -> None:
    command = make_command()
    result = make_result(command, **{field: value})

    with pytest.raises(StageResultMismatchError, match=message):
        validate_stage_result(command, result)


def test_duplicate_result_is_rejected_within_decision_context() -> None:
    command = make_command()
    result = make_result(command)

    with pytest.raises(DuplicateStageResultError, match="already processed"):
        validate_stage_result(
            command,
            result,
            processed_command_ids={command.command_id},
        )


def test_same_inputs_clock_and_uuid_factory_produce_same_decision() -> None:
    job = make_job(status=ProductionJobStatus.QUEUED, stage=ProductionStage.CREATED)
    configuration = PipelineConfiguration(input_artifact_ids=(ARTIFACT_ID,))

    first = make_orchestrator().decide(job, configuration, next_sequence_number=7)
    second = make_orchestrator().decide(job, configuration, next_sequence_number=7)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
