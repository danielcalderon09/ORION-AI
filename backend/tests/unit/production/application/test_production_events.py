"""Serialization tests for production domain events."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.src.production.application.events import (
    ProductionCancellationRequested,
    ProductionEventUnion,
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
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ProductionStage

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000001")
EVENT_ID = UUID("20000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("30000000-0000-4000-8000-000000000001")
ARTIFACT_ID = UUID("40000000-0000-4000-8000-000000000001")


def envelope(sequence: int) -> dict:
    return {
        "event_id": UUID(f"20000000-0000-4000-8000-{sequence + 1:012d}"),
        "job_id": JOB_ID,
        "occurred_at": NOW,
        "sequence_number": sequence,
        "correlation_id": JOB_ID,
    }


def test_all_events_serialize_and_deserialize_stably() -> None:
    events = [
        ProductionJobCreated(**envelope(0)),
        ProductionJobQueued(**envelope(1)),
        ProductionStageStarted(
            **envelope(2), stage=ProductionStage.PLANNING, command_id=COMMAND_ID, attempt_number=1
        ),
        ProductionStageProgressed(
            **envelope(3),
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            progress_percent=50,
            message="halfway",
        ),
        ProductionStageSucceeded(
            **envelope(4),
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            output_artifact_ids=(ARTIFACT_ID,),
        ),
        ProductionStageFailed(
            **envelope(5),
            stage=ProductionStage.PLANNING,
            command_id=COMMAND_ID,
            outcome=StageOutcome.FAILED_TRANSIENT,
            error_code="temporary",
        ),
        ProductionRetryScheduled(
            **envelope(6),
            stage=ProductionStage.PLANNING,
            next_attempt_number=2,
            retry_at=NOW + timedelta(seconds=30),
        ),
        ProductionUserActionRequired(
            **envelope(7),
            stage=ProductionStage.PLANNING,
            action_code="confirm",
            instructions="Confirm the plan",
        ),
        ProductionCancellationRequested(**envelope(8), reason="user requested"),
        ProductionJobCancelled(**envelope(9), reason="safe cancellation"),
        ProductionJobCompleted(**envelope(10), long_form_artifact_id=ARTIFACT_ID),
    ]
    adapter = TypeAdapter(ProductionEventUnion)

    for event in events:
        serialized = event.model_dump_json()
        restored = adapter.validate_json(serialized)
        assert restored == event
        assert restored.model_dump(mode="json") == event.model_dump(mode="json")


def test_event_rejects_negative_sequence_number() -> None:
    payload = envelope(0)
    payload["sequence_number"] = -1

    with pytest.raises(ValidationError, match="sequence_number"):
        ProductionJobQueued(**payload)


def test_event_rejects_naive_timestamp() -> None:
    payload = envelope(0)
    payload["occurred_at"] = datetime(2026, 7, 17, 12, 0)

    with pytest.raises(ValidationError, match="timezone-aware"):
        ProductionJobQueued(**payload)
