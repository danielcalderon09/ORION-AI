"""Deterministic contract factories shared by persistence tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.events import (
    ProductionCancellationRequested,
    ProductionJobCancelled,
    ProductionJobCompleted,
    ProductionJobCreated,
    ProductionJobQueued,
    ProductionRetryScheduled,
    ProductionStageFailed,
    ProductionStageProgressed,
    ProductionStageStarted,
    ProductionStageSucceeded,
    ProductionUserActionRequired,
)
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionJobStatus,
    ProductionStage,
)
from backend.src.production.domain.production_job import ProductionJob

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000001")
NEXT_COMMAND_ID = UUID("20000000-0000-4000-8000-000000000002")
ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000001")


class UUIDSequence:
    def __init__(self, *, start: int = 1) -> None:
        self._next_value = start

    def __call__(self) -> UUID:
        value = UUID(f"90000000-0000-4000-8000-{self._next_value:012d}")
        self._next_value += 1
        return value


def make_job(
    *,
    job_id: UUID = JOB_ID,
    status: ProductionJobStatus = ProductionJobStatus.CREATED,
    stage: ProductionStage = ProductionStage.CREATED,
    offset_seconds: int = 0,
) -> ProductionJob:
    created = NOW + timedelta(seconds=offset_seconds)
    return ProductionJob(
        job_id=job_id,
        prompt=f"Create video {job_id}",
        status=status,
        current_stage=stage,
        created_at=created,
        updated_at=created,
        configuration_snapshot={"language": "es", "nested": {"quality": 1}},
    )


def make_artifact(
    *,
    artifact_id: UUID = ARTIFACT_ID,
    job_id: UUID = JOB_ID,
    relative_path: str = "assets/result.png",
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        job_id=job_id,
        artifact_type=ArtifactType.SOURCE_IMAGE,
        relative_path=relative_path,
        mime_type="image/png",
        status=ArtifactStatus.READY,
        size_bytes=128,
        sha256="a" * 64,
        width=1080,
        height=1920,
        provider="test",
        metadata={"source": "fixture"},
    )


def make_command(
    *,
    command_id: UUID = COMMAND_ID,
    job_id: UUID = JOB_ID,
    stage: ProductionStage = ProductionStage.PLANNING,
    attempt_number: int = 1,
    idempotency_key: str | None = None,
    input_artifact_ids: tuple[UUID, ...] = (),
) -> StageCommand:
    return StageCommand(
        command_id=command_id,
        job_id=job_id,
        stage=stage,
        attempt_number=attempt_number,
        idempotency_key=idempotency_key or f"test:{job_id}:{stage.value}:{attempt_number}",
        input_artifact_ids=input_artifact_ids,
        configuration_snapshot={"language": "es"},
        created_at=NOW,
    )


def make_result(
    command: StageCommand,
    *,
    outcome: StageOutcome = StageOutcome.SUCCEEDED,
    output_artifact_ids: tuple[UUID, ...] = (ARTIFACT_ID,),
) -> StageResult:
    error_code = None
    error_message = None
    retry_after_seconds = None
    progress = 100.0
    if outcome in {StageOutcome.FAILED_TRANSIENT, StageOutcome.FAILED_PERMANENT}:
        error_code = "stage_failure"
        error_message = "Stage failed"
        output_artifact_ids = ()
        progress = 40.0
    if outcome is StageOutcome.FAILED_TRANSIENT:
        retry_after_seconds = 30.0
    return StageResult(
        command_id=command.command_id,
        job_id=command.job_id,
        stage=command.stage,
        outcome=outcome,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=5),
        progress_percent=progress,
        output_artifact_ids=output_artifact_ids,
        error_code=error_code,
        error_message=error_message,
        retry_after_seconds=retry_after_seconds,
        metadata={"worker": "test"},
    )


def sample_events() -> tuple:
    common = {"job_id": JOB_ID, "correlation_id": JOB_ID, "occurred_at": NOW}
    return (
        ProductionJobCreated(
            event_id=UUID("40000000-0000-4000-8000-000000000001"),
            sequence_number=0,
            **common,
        ),
        ProductionJobQueued(
            event_id=UUID("40000000-0000-4000-8000-000000000002"),
            sequence_number=1,
            **common,
        ),
        ProductionStageStarted(
            event_id=UUID("40000000-0000-4000-8000-000000000003"),
            sequence_number=2,
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            attempt_number=1,
            **common,
        ),
        ProductionStageProgressed(
            event_id=UUID("40000000-0000-4000-8000-000000000004"),
            sequence_number=3,
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            progress_percent=50,
            message="halfway",
            **common,
        ),
        ProductionStageSucceeded(
            event_id=UUID("40000000-0000-4000-8000-000000000005"),
            sequence_number=4,
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            output_artifact_ids=(ARTIFACT_ID,),
            **common,
        ),
        ProductionStageFailed(
            event_id=UUID("40000000-0000-4000-8000-000000000006"),
            sequence_number=5,
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            outcome=StageOutcome.FAILED_TRANSIENT,
            error_code="temporary",
            **common,
        ),
        ProductionRetryScheduled(
            event_id=UUID("40000000-0000-4000-8000-000000000007"),
            sequence_number=6,
            stage=ProductionStage.PLANNING,
            next_attempt_number=2,
            retry_at=NOW + timedelta(seconds=30),
            **common,
        ),
        ProductionUserActionRequired(
            event_id=UUID("40000000-0000-4000-8000-000000000008"),
            sequence_number=7,
            stage=ProductionStage.PLANNING,
            action_code="confirm",
            instructions="Confirm plan",
            **common,
        ),
        ProductionCancellationRequested(
            event_id=UUID("40000000-0000-4000-8000-000000000009"),
            sequence_number=8,
            reason="user",
            **common,
        ),
        ProductionJobCancelled(
            event_id=UUID("40000000-0000-4000-8000-000000000010"),
            sequence_number=9,
            reason="safe",
            **common,
        ),
        ProductionJobCompleted(
            event_id=UUID("40000000-0000-4000-8000-000000000011"),
            sequence_number=10,
            long_form_artifact_id=ARTIFACT_ID,
            **common,
        ),
    )
