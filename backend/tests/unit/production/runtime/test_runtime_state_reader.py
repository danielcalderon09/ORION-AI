from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.events import ProductionRetryScheduled
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.mappers.command_mapper import (
    StageCommandMapper,
)
from backend.src.production.infrastructure.persistence.mappers.event_mapper import (
    ProductionEventMapper,
)
from backend.src.production.infrastructure.persistence.mappers.production_job_mapper import (
    ProductionJobMapper,
)
from backend.src.production.infrastructure.persistence.models import (
    ProductionEventRecord,
    StageCommandRecord,
)
from backend.src.production.runtime import (
    MultiplePendingStageCommandsError,
    RuntimeStateReader,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def add_job(session_factory, job_id: UUID, *, offset: int = 0) -> ProductionJob:
    job = ProductionJob(
        job_id=job_id,
        prompt="State reader test",
        status=ProductionJobStatus.WAITING_FOR_RETRY,
        current_stage=ProductionStage.PLANNING,
        created_at=NOW + timedelta(seconds=offset),
        updated_at=NOW + timedelta(seconds=offset),
    )
    with session_factory() as session:
        session.add(ProductionJobMapper.to_record(job, db_now=NOW))
        session.commit()
    return job


def make_command(job_id: UUID, number: int, *, attempt: int = 1) -> StageCommand:
    return StageCommand(
        command_id=UUID(f"20000000-0000-4000-8000-{number:012d}"),
        job_id=job_id,
        stage=ProductionStage.PLANNING,
        attempt_number=attempt,
        idempotency_key=f"reader:{job_id}:{number}",
        created_at=NOW + timedelta(seconds=number),
    )


def add_command(session_factory, command: StageCommand, *, processed: bool = False) -> None:
    record = StageCommandMapper.to_record(command)
    if processed:
        record.processed_at = NOW
    with session_factory() as session:
        session.add(record)
        session.commit()


def add_retry_event(session_factory, job_id: UUID, *, sequence: int = 0) -> None:
    event = ProductionRetryScheduled(
        event_id=UUID(f"40000000-0000-4000-8000-{job_id.int % 10**12:012d}"),
        job_id=job_id,
        occurred_at=NOW,
        sequence_number=sequence,
        correlation_id=job_id,
        stage=ProductionStage.PLANNING,
        next_attempt_number=2,
        retry_at=NOW + timedelta(seconds=30),
    )
    with session_factory() as session:
        session.add(ProductionEventMapper.to_record(event, db_now=NOW))
        session.commit()


def test_reader_loads_job_and_none_without_pending_command(runtime_database) -> None:
    _, session_factory = runtime_database
    job_id = UUID("10000000-0000-4000-8000-000000000361")
    job = add_job(session_factory, job_id)
    reader = RuntimeStateReader(session_factory)
    assert reader.load_job(job_id) == job
    assert reader.find_unprocessed_command(job_id) is None


def test_reader_returns_one_pending_command_and_rejects_multiple(runtime_database) -> None:
    _, session_factory = runtime_database
    job_id = UUID("10000000-0000-4000-8000-000000000362")
    add_job(session_factory, job_id)
    first = make_command(job_id, 1)
    add_command(session_factory, first)
    reader = RuntimeStateReader(session_factory)
    assert reader.find_unprocessed_command(job_id) == first
    add_command(session_factory, make_command(job_id, 2, attempt=2))
    with pytest.raises(MultiplePendingStageCommandsError):
        reader.find_unprocessed_command(job_id)


def test_reader_calculates_sequence_attempt_and_does_not_modify(runtime_database) -> None:
    _, session_factory = runtime_database
    job_id = UUID("10000000-0000-4000-8000-000000000363")
    add_job(session_factory, job_id)
    add_retry_event(session_factory, job_id, sequence=0)
    add_command(session_factory, make_command(job_id, 3, attempt=2), processed=True)
    reader = RuntimeStateReader(session_factory)
    with session_factory() as session:
        before = session.scalar(select(func.count(StageCommandRecord.command_id)))
    assert reader.next_event_sequence(job_id) == 1
    assert reader.next_attempt_number(job_id, ProductionStage.PLANNING) == 3
    with session_factory() as session:
        after = session.scalar(select(func.count(StageCommandRecord.command_id)))
    assert before == after


def test_reader_lists_retry_candidates_in_deterministic_order(runtime_database) -> None:
    _, session_factory = runtime_database
    ids = (
        UUID("10000000-0000-4000-8000-000000000365"),
        UUID("10000000-0000-4000-8000-000000000364"),
    )
    add_job(session_factory, ids[0], offset=1)
    add_job(session_factory, ids[1], offset=0)
    for job_id in ids:
        add_retry_event(session_factory, job_id)
    candidates = RuntimeStateReader(session_factory).list_retry_candidates()
    assert tuple(item.job.job_id for item in candidates) == (ids[1], ids[0])
    assert all(item.next_sequence_number == 1 for item in candidates)
    with session_factory() as session:
        assert session.scalar(select(func.count(ProductionEventRecord.event_id))) == 2
