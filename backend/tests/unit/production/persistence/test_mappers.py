"""Round-trip and integrity tests for explicit persistence mappers."""

from datetime import timedelta

import pytest

from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers import (
    ArtifactMapper,
    ProductionEventMapper,
    ProductionJobMapper,
    StageCommandMapper,
    StageResultMapper,
)
from backend.src.production.infrastructure.persistence.models import ProductionEventRecord
from backend.tests.unit.production.persistence.factories import (
    JOB_ID,
    NOW,
    make_artifact,
    make_command,
    make_job,
    make_result,
    sample_events,
)


def test_production_job_round_trip_preserves_types_and_json() -> None:
    job = make_job(status=ProductionJobStatus.RUNNING, stage=ProductionStage.PLANNING)
    record = ProductionJobMapper.to_record(job, db_now=NOW)

    restored = ProductionJobMapper.to_domain(record)

    assert restored == job
    assert restored.status is ProductionJobStatus.RUNNING
    assert restored.current_stage is ProductionStage.PLANNING
    assert restored.created_at.utcoffset() == timedelta(0)
    assert restored.configuration_snapshot == job.configuration_snapshot


def test_artifact_round_trip_preserves_uuid_enum_and_metadata() -> None:
    artifact = make_artifact()

    restored = ArtifactMapper.to_domain(ArtifactMapper.to_record(artifact, db_now=NOW))

    assert restored == artifact
    assert restored.artifact_id == artifact.artifact_id
    assert restored.metadata == {"source": "fixture"}


def test_stage_command_round_trip() -> None:
    command = make_command()

    restored = StageCommandMapper.to_domain(StageCommandMapper.to_record(command))

    assert restored == command
    assert restored.created_at.tzinfo is not None


def test_stage_result_round_trip() -> None:
    result = make_result(make_command())

    restored = StageResultMapper.to_domain(StageResultMapper.to_record(result, db_now=NOW))

    assert restored == result
    assert restored.metadata == {"worker": "test"}


def test_all_event_variants_round_trip() -> None:
    for event in sample_events():
        restored = ProductionEventMapper.to_domain(
            ProductionEventMapper.to_record(event, db_now=NOW)
        )
        assert restored == event
        assert type(restored) is type(event)


def test_unknown_event_type_is_rejected() -> None:
    record = ProductionEventRecord(
        event_id="40000000-0000-4000-8000-000000000099",
        job_id=str(JOB_ID),
        event_type="future_unknown_event",
        schema_version="1.0.0",
        occurred_at=NOW,
        sequence_number=0,
        correlation_id=str(JOB_ID),
        causation_id=None,
        payload={},
        metadata_json={},
        created_at=NOW,
    )

    with pytest.raises(ProductionRecordIntegrityError, match="unknown production event"):
        ProductionEventMapper.to_domain(record)


def test_inconsistent_job_record_is_rejected() -> None:
    record = ProductionJobMapper.to_record(make_job(), db_now=NOW)
    record.status = "not-a-status"

    with pytest.raises(ProductionRecordIntegrityError, match="invalid production job record"):
        ProductionJobMapper.to_domain(record)
