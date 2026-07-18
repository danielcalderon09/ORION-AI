"""Atomicity and idempotency tests for OrchestrationDecisionStore."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
    ProductionEventSequenceError,
    ProductionIdempotencyConflictError,
)
from backend.src.production.infrastructure.persistence.mappers import StageCommandMapper
from backend.src.production.infrastructure.persistence.models import (
    ProductionEventRecord,
    ProductionJobRecord,
    ProductionStageRunRecord,
    StageCommandRecord,
    StageResultRecord,
)
from backend.src.production.infrastructure.persistence.repositories import (
    SQLAlchemyProductionJobRepository,
)
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.tests.unit.production.persistence.factories import (
    NOW,
    UUIDSequence,
    make_artifact,
    make_command,
    make_job,
    make_result,
)


def orchestrator(*, start: int) -> ProductionOrchestrator:
    return ProductionOrchestrator(clock=lambda: NOW, uuid_factory=UUIDSequence(start=start))


async def bootstrap_running(session_factory):
    store = OrchestrationDecisionStore(session_factory, clock=lambda: NOW)
    created = make_job()
    queued_decision = orchestrator(start=1).decide(
        created,
        PipelineConfiguration(),
        next_sequence_number=0,
    )
    await store.persist_decision(previous_job=created, decision=queued_decision)
    running_decision = orchestrator(start=10).decide(
        queued_decision.updated_job,
        PipelineConfiguration(),
        next_sequence_number=1,
    )
    await store.persist_decision(
        previous_job=queued_decision.updated_job,
        decision=running_decision,
    )
    assert running_decision.next_command is not None
    return store, running_decision.updated_job, running_decision.next_command


def table_count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.asyncio
async def test_created_to_queued_decision_is_persisted(production_database) -> None:
    _, session_factory = production_database
    store = OrchestrationDecisionStore(session_factory, clock=lambda: NOW)
    created = make_job()
    decision = orchestrator(start=1).decide(created, PipelineConfiguration())

    receipt = await store.persist_decision(previous_job=created, decision=decision)

    assert receipt.job.status is ProductionJobStatus.QUEUED
    assert receipt.row_version == 1
    with session_factory() as session:
        assert table_count(session, ProductionJobRecord) == 1
        assert table_count(session, ProductionEventRecord) == 1
        assert table_count(session, StageCommandRecord) == 0


@pytest.mark.asyncio
async def test_queued_to_running_persists_command_event_and_stage_run(
    production_database,
) -> None:
    _, session_factory = production_database
    _, running_job, command = await bootstrap_running(session_factory)

    assert running_job.status is ProductionJobStatus.RUNNING
    assert command.stage is ProductionStage.PLANNING
    with session_factory() as session:
        assert table_count(session, StageCommandRecord) == 1
        assert table_count(session, ProductionEventRecord) == 2
        assert table_count(session, ProductionStageRunRecord) == 1


@pytest.mark.asyncio
async def test_succeeded_result_atomically_advances_and_creates_next_command(
    production_database,
) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command)
    decision = orchestrator(start=20).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=2,
    )

    receipt = await store.persist_decision(
        previous_job=running_job,
        decision=decision,
        processed_command=command,
        processed_result=result,
        artifacts=(make_artifact(),),
    )

    assert receipt.job.current_stage is ProductionStage.SCRIPTING
    with session_factory() as session:
        assert table_count(session, StageCommandRecord) == 2
        assert table_count(session, StageResultRecord) == 1
        assert table_count(session, ProductionEventRecord) == 4
        assert table_count(session, ProductionStageRunRecord) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_events"),
    [
        (StageOutcome.FAILED_TRANSIENT, ProductionJobStatus.WAITING_FOR_RETRY, 4),
        (StageOutcome.FAILED_PERMANENT, ProductionJobStatus.FAILED, 3),
    ],
)
async def test_failed_results_are_persisted_atomically(
    production_database,
    outcome: StageOutcome,
    expected_status: ProductionJobStatus,
    expected_events: int,
) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command, outcome=outcome)
    decision = orchestrator(start=30).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=2,
    )

    await store.persist_decision(
        previous_job=running_job,
        decision=decision,
        processed_command=command,
        processed_result=result,
    )

    with session_factory() as session:
        job_record = session.get(ProductionJobRecord, str(running_job.job_id))
        assert job_record is not None and job_record.status == expected_status.value
        assert table_count(session, StageResultRecord) == 1
        assert table_count(session, ProductionEventRecord) == expected_events


@pytest.mark.asyncio
async def test_last_stage_persists_completed_job(production_database) -> None:
    _, session_factory = production_database
    running_job = make_job(
        status=ProductionJobStatus.RUNNING,
        stage=ProductionStage.VALIDATING_RENDER,
    )
    command = make_command(stage=ProductionStage.VALIDATING_RENDER)
    result = make_result(command)
    with session_factory() as session:
        repository = SQLAlchemyProductionJobRepository(session, clock=lambda: NOW)
        await repository.add(running_job)
        session.commit()
    decision = orchestrator(start=40).decide(
        running_job,
        PipelineConfiguration(generate_clips_after_render=False),
        last_command=command,
        last_result=result,
        next_sequence_number=0,
    )
    store = OrchestrationDecisionStore(session_factory, clock=lambda: NOW)

    await store.persist_decision(
        previous_job=running_job,
        decision=decision,
        processed_command=command,
        processed_result=result,
        artifacts=(make_artifact(),),
    )

    with session_factory() as session:
        record = session.get(ProductionJobRecord, str(running_job.job_id))
        assert record is not None
        assert record.status == ProductionJobStatus.COMPLETED.value
        assert record.current_stage == ProductionStage.COMPLETED.value


@pytest.mark.asyncio
async def test_invalid_event_sequence_rolls_back_job_command_and_result(
    production_database,
) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command)
    invalid = orchestrator(start=50).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=99,
    )

    with pytest.raises(ProductionEventSequenceError, match="expected event sequence"):
        await store.persist_decision(
            previous_job=running_job,
            decision=invalid,
            processed_command=command,
            processed_result=result,
            artifacts=(make_artifact(),),
        )

    with session_factory() as session:
        record = session.get(ProductionJobRecord, str(running_job.job_id))
        assert record is not None and record.current_stage == ProductionStage.PLANNING.value
        assert table_count(session, StageCommandRecord) == 1
        assert table_count(session, StageResultRecord) == 0
        assert table_count(session, ProductionEventRecord) == 2


@pytest.mark.asyncio
async def test_command_conflict_rolls_back_complete_decision(production_database) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command)
    decision = orchestrator(start=60).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=2,
    )
    assert decision.next_command is not None
    conflict = make_command(
        command_id=UUID("20000000-0000-4000-8000-000000000099"),
        stage=decision.next_command.stage,
        idempotency_key=decision.next_command.idempotency_key,
    )
    with session_factory() as session:
        session.add(StageCommandMapper.to_record(conflict))
        session.commit()

    with pytest.raises(ProductionIdempotencyConflictError, match="idempotency key"):
        await store.persist_decision(
            previous_job=running_job,
            decision=decision,
            processed_command=command,
            processed_result=result,
            artifacts=(make_artifact(),),
        )

    with session_factory() as session:
        record = session.get(ProductionJobRecord, str(running_job.job_id))
        assert record is not None and record.current_stage == ProductionStage.PLANNING.value
        assert table_count(session, StageResultRecord) == 0


@pytest.mark.asyncio
async def test_stale_previous_job_rolls_back_before_decision_writes(production_database) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command)
    decision = orchestrator(start=70).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=2,
    )
    with session_factory() as session:
        repository = SQLAlchemyProductionJobRepository(
            session,
            clock=lambda: NOW + timedelta(seconds=1),
        )
        current = await repository.get(running_job.job_id)
        assert current is not None
        await repository.save(
            current.model_copy(
                update={
                    "status": ProductionJobStatus.CANCEL_REQUESTED,
                    "updated_at": NOW + timedelta(seconds=1),
                }
            )
        )
        session.commit()

    with pytest.raises(ProductionConcurrencyError, match="changed concurrently"):
        await store.persist_decision(
            previous_job=running_job,
            decision=decision,
            processed_command=command,
            processed_result=result,
            artifacts=(make_artifact(),),
        )

    with session_factory() as session:
        assert table_count(session, StageResultRecord) == 0
        assert table_count(session, ProductionEventRecord) == 2


@pytest.mark.asyncio
async def test_repeating_same_decision_is_idempotent_without_duplicates(
    production_database,
) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command)
    decision = orchestrator(start=80).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=2,
    )
    arguments = {
        "previous_job": running_job,
        "decision": decision,
        "processed_command": command,
        "processed_result": result,
        "artifacts": (make_artifact(),),
    }

    first = await store.persist_decision(**arguments)
    second = await store.persist_decision(**arguments)

    assert not first.idempotent_replay
    assert second.idempotent_replay
    assert second.row_version == first.row_version
    with session_factory() as session:
        assert table_count(session, StageCommandRecord) == 2
        assert table_count(session, StageResultRecord) == 1
        assert table_count(session, ProductionEventRecord) == 4
        assert table_count(session, ProductionStageRunRecord) == 2


@pytest.mark.asyncio
async def test_replay_with_changed_event_content_is_conflict(production_database) -> None:
    _, session_factory = production_database
    store, running_job, command = await bootstrap_running(session_factory)
    result = make_result(command)
    decision = orchestrator(start=90).decide(
        running_job,
        PipelineConfiguration(),
        last_command=command,
        last_result=result,
        next_sequence_number=2,
    )
    arguments = {
        "previous_job": running_job,
        "decision": decision,
        "processed_command": command,
        "processed_result": result,
        "artifacts": (make_artifact(),),
    }
    await store.persist_decision(**arguments)
    changed_event = decision.events[0].model_copy(update={"metadata": {"changed": True}})
    changed_decision = decision.model_copy(
        update={"events": (changed_event, *decision.events[1:])}
    )

    with pytest.raises(ProductionIdempotencyConflictError, match="event"):
        await store.persist_decision(
            **{**arguments, "decision": changed_decision}
        )
