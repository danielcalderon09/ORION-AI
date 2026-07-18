from uuid import UUID

import pytest

from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.runtime import (
    RuntimeStateReader,
    ThreadedRuntimeBlockingExecutor,
    ThreadedRuntimeDecisionPersister,
)
from backend.tests.unit.production.runtime.conftest import MutableClock, UUIDSequence


@pytest.mark.asyncio
async def test_threaded_read_and_persistence_own_their_sessions(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(8)
    job = ProductionJob(
        job_id=UUID("10000000-0000-4000-8000-000000000395"),
        prompt="Thread isolation test",
        created_at=clock(),
        updated_at=clock(),
    )
    decision = ProductionOrchestrator(clock=clock, uuid_factory=uuids).decide(
        job,
        PipelineConfiguration(),
    )
    persister = ThreadedRuntimeDecisionPersister(
        OrchestrationDecisionStore(session_factory, clock=clock)
    )
    await persister.persist_decision(previous_job=job, decision=decision)
    loaded = await ThreadedRuntimeBlockingExecutor().run(
        RuntimeStateReader(session_factory).load_job,
        job.job_id,
    )
    assert loaded.status is ProductionJobStatus.QUEUED
