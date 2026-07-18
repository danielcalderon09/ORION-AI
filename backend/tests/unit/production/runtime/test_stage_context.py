from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.runtime import (
    StageContext,
    StageContextFactory,
    StageContextMismatchError,
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000351")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000351")
ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000351")


def make_job(*, job_id: UUID = JOB_ID) -> ProductionJob:
    return ProductionJob(
        job_id=job_id,
        prompt="Context test",
        status=ProductionJobStatus.RUNNING,
        current_stage=ProductionStage.PLANNING,
        created_at=NOW,
        updated_at=NOW,
    )


def make_command(*, job_id: UUID = JOB_ID) -> StageCommand:
    return StageCommand(
        command_id=COMMAND_ID,
        job_id=job_id,
        stage=ProductionStage.PLANNING,
        attempt_number=2,
        idempotency_key="context:test",
        input_artifact_ids=(ARTIFACT_ID,),
        configuration_snapshot={"language": "es", "style": {"tone": "calm"}},
        created_at=NOW,
    )


def test_stage_context_round_trip_is_stable_and_immutable() -> None:
    context = StageContextFactory().create(job=make_job(), command=make_command())
    restored = StageContext.model_validate_json(context.model_dump_json())
    assert restored == context
    with pytest.raises(ValidationError):
        context.attempt_number = 3


@pytest.mark.parametrize(
    "path",
    (r"C:\\orion\\job", "/home/orion/job", "production/job/../secret", r"\\server\\share"),
)
def test_stage_context_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        StageContext(
            job_id=JOB_ID,
            command_id=COMMAND_ID,
            stage=ProductionStage.PLANNING,
            attempt_number=1,
            workspace_relative_path=path,
            correlation_id=JOB_ID,
        )


def test_factory_preserves_configuration_artifacts_and_posix_workspace() -> None:
    context = StageContextFactory().create(job=make_job(), command=make_command())
    assert context.job_configuration == {"language": "es", "style": {"tone": "calm"}}
    assert context.input_artifact_ids == (ARTIFACT_ID,)
    assert context.workspace_relative_path == (
        f"production/{JOB_ID}/planning/attempt-2"
    )
    assert "\\" not in context.workspace_relative_path


def test_factory_rejects_job_command_mismatch() -> None:
    with pytest.raises(StageContextMismatchError):
        StageContextFactory().create(
            job=make_job(),
            command=make_command(job_id=UUID("10000000-0000-4000-8000-000000000399")),
        )


def test_stage_context_rejects_credentials() -> None:
    with pytest.raises(ValidationError):
        StageContext(
            job_id=JOB_ID,
            command_id=COMMAND_ID,
            stage=ProductionStage.PLANNING,
            attempt_number=1,
            job_configuration={"provider": {"api_key": "must-not-enter-context"}},
            workspace_relative_path="production/job/planning/attempt-1",
            correlation_id=JOB_ID,
        )
