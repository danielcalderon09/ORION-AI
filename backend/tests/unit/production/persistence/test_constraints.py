"""Database-level constraints for production persistence."""

from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.src.production.infrastructure.persistence.mappers import (
    ArtifactMapper,
    ProductionEventMapper,
    ProductionJobMapper,
    StageCommandMapper,
    StageResultMapper,
)
from backend.src.production.infrastructure.persistence.models import (
    ProductionStageRunRecord,
    ProductionStageRunStatus,
    StageResultRecord,
)
from backend.tests.unit.production.persistence.factories import (
    ARTIFACT_ID,
    JOB_ID,
    NOW,
    make_artifact,
    make_command,
    make_job,
    make_result,
    sample_events,
)


def seed_job(session) -> None:
    session.add(ProductionJobMapper.to_record(make_job(), db_now=NOW))
    session.commit()


def seed_command(session):
    command = make_command()
    session.add(StageCommandMapper.to_record(command))
    session.commit()
    return command


def test_command_id_and_idempotency_key_are_unique(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        seed_job(session)
        command = seed_command(session)
        duplicate_key = make_command(
            command_id=UUID("20000000-0000-4000-8000-000000000099"),
            idempotency_key=command.idempotency_key,
        )
        session.add(StageCommandMapper.to_record(duplicate_key))

        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        duplicate_id = StageCommandMapper.to_record(command)
        session.add(duplicate_id)
        with pytest.raises(IntegrityError):
            session.commit()


def test_only_one_result_per_command(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        seed_job(session)
        command = seed_command(session)
        result = make_result(command)
        session.add(StageResultMapper.to_record(result, db_now=NOW))
        session.commit()

        session.add(StageResultMapper.to_record(result, db_now=NOW))
        with pytest.raises(IntegrityError):
            session.commit()


def test_event_sequence_is_unique_per_job(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        seed_job(session)
        event = sample_events()[0]
        session.add(ProductionEventMapper.to_record(event, db_now=NOW))
        session.commit()
        conflicting = event.model_copy(
            update={"event_id": UUID("40000000-0000-4000-8000-000000000099")}
        )
        session.add(ProductionEventMapper.to_record(conflicting, db_now=NOW))

        with pytest.raises(IntegrityError):
            session.commit()


def test_stage_attempt_is_unique_and_positive(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        seed_job(session)
        command = seed_command(session)
        first = ProductionStageRunRecord(
            stage_run_id="50000000-0000-4000-8000-000000000001",
            job_id=str(JOB_ID),
            stage=command.stage.value,
            attempt_number=1,
            status=ProductionStageRunStatus.PENDING.value,
            command_id=str(command.command_id),
            result_id=None,
            idempotency_key=command.idempotency_key,
            started_at=None,
            finished_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(first)
        session.commit()
        duplicate_attempt = ProductionStageRunRecord(
            stage_run_id="50000000-0000-4000-8000-000000000002",
            job_id=str(JOB_ID),
            stage=command.stage.value,
            attempt_number=1,
            status=ProductionStageRunStatus.PENDING.value,
            command_id=None,
            result_id=None,
            idempotency_key="another-key",
            started_at=None,
            finished_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(duplicate_attempt)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        invalid_attempt = ProductionStageRunRecord(
            stage_run_id="50000000-0000-4000-8000-000000000003",
            job_id=str(JOB_ID),
            stage=command.stage.value,
            attempt_number=0,
            status=ProductionStageRunStatus.PENDING.value,
            command_id=None,
            result_id=None,
            idempotency_key="invalid-attempt-key",
            started_at=None,
            finished_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(invalid_attempt)
        with pytest.raises(IntegrityError):
            session.commit()


def test_artifact_path_is_unique_per_job(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        seed_job(session)
        artifact = make_artifact()
        session.add(ArtifactMapper.to_record(artifact, db_now=NOW))
        session.commit()
        conflict = make_artifact(
            artifact_id=UUID("30000000-0000-4000-8000-000000000099")
        )
        session.add(ArtifactMapper.to_record(conflict, db_now=NOW))

        with pytest.raises(IntegrityError):
            session.commit()


def test_sqlite_foreign_keys_are_active(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        command = make_command(job_id=UUID("10000000-0000-4000-8000-000000000099"))
        session.add(StageCommandMapper.to_record(command))

        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_command_attempt_and_result_progress_checks(production_database) -> None:
    _, session_factory = production_database
    with session_factory() as session:
        seed_job(session)
        command = make_command()
        command_record = StageCommandMapper.to_record(command)
        command_record.attempt_number = 0
        session.add(command_record)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        valid_command = seed_command(session)
        result_record: StageResultRecord = StageResultMapper.to_record(
            make_result(valid_command),
            db_now=NOW,
        )
        result_record.progress_percent = 101
        session.add(result_record)
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("path", ["C:/private/file.mp4", "/tmp/file.mp4"])
def test_absolute_paths_cannot_reach_artifact_persistence(path: str) -> None:
    with pytest.raises(ValidationError):
        make_artifact(artifact_id=ARTIFACT_ID, relative_path=path)
