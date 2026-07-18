"""Unit tests for production stage command and result contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.enums import ProductionStage

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000001")
ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000001")


def make_command(**updates) -> StageCommand:
    payload = {
        "command_id": COMMAND_ID,
        "job_id": JOB_ID,
        "stage": ProductionStage.PLANNING,
        "attempt_number": 1,
        "idempotency_key": "production-stage-v1:fixed",
        "input_artifact_ids": (ARTIFACT_ID,),
        "configuration_snapshot": {"language": "es"},
        "created_at": NOW,
    }
    payload.update(updates)
    return StageCommand.model_validate(payload)


def make_result(**updates) -> StageResult:
    payload = {
        "command_id": COMMAND_ID,
        "job_id": JOB_ID,
        "stage": ProductionStage.PLANNING,
        "outcome": StageOutcome.SUCCEEDED,
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=1),
        "progress_percent": 100,
        "output_artifact_ids": (ARTIFACT_ID,),
    }
    payload.update(updates)
    return StageResult.model_validate(payload)


def test_stage_command_valid() -> None:
    command = make_command()

    assert command.attempt_number == 1
    assert command.stage is ProductionStage.PLANNING
    assert command.model_dump(mode="json")["command_id"] == str(COMMAND_ID)


def test_stage_command_rejects_attempt_zero() -> None:
    with pytest.raises(ValidationError, match="attempt_number"):
        make_command(attempt_number=0)


def test_stage_command_rejects_duplicate_artifact_ids() -> None:
    with pytest.raises(ValidationError, match="input_artifact_ids must be unique"):
        make_command(input_artifact_ids=(ARTIFACT_ID, ARTIFACT_ID))


def test_stage_result_succeeded_valid() -> None:
    result = make_result()

    assert result.outcome is StageOutcome.SUCCEEDED
    assert result.progress_percent == 100


def test_stage_result_succeeded_requires_full_progress() -> None:
    with pytest.raises(ValidationError, match="progress_percent=100"):
        make_result(progress_percent=99)


@pytest.mark.parametrize(
    "outcome",
    [StageOutcome.FAILED_TRANSIENT, StageOutcome.FAILED_PERMANENT],
)
def test_failed_stage_result_requires_error_code(outcome: StageOutcome) -> None:
    with pytest.raises(ValidationError, match="failed results require error_code"):
        make_result(outcome=outcome, progress_percent=50, output_artifact_ids=())


def test_retry_after_seconds_only_allowed_for_transient_failure() -> None:
    transient = make_result(
        outcome=StageOutcome.FAILED_TRANSIENT,
        progress_percent=50,
        output_artifact_ids=(),
        error_code="temporary",
        retry_after_seconds=10,
    )
    assert transient.retry_after_seconds == 10

    with pytest.raises(ValidationError, match="only valid for failed_transient"):
        make_result(
            outcome=StageOutcome.FAILED_PERMANENT,
            progress_percent=50,
            output_artifact_ids=(),
            error_code="permanent",
            retry_after_seconds=10,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("started_at", datetime(2026, 7, 17, 12, 0)),
        ("finished_at", NOW - timedelta(seconds=1)),
    ],
)
def test_stage_result_rejects_invalid_timestamps(field: str, value: datetime) -> None:
    with pytest.raises(ValidationError):
        make_result(**{field: value})


def test_cancelled_result_cannot_produce_artifacts() -> None:
    with pytest.raises(ValidationError, match="cancelled results cannot produce"):
        make_result(outcome=StageOutcome.CANCELLED)
