from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.runtime import (
    ImmediateRuntimeBlockingExecutor,
    ImmediateRuntimeDecisionPersister,
    ProductionLeaseManager,
    ProductionRecoveryService,
    RuntimeStateReader,
    SQLAlchemyLeaseRepository,
)
from backend.tests.unit.production.runtime.conftest import MutableClock, UUIDSequence, build_worker
from backend.tests.unit.production.runtime.test_worker import (
    enqueue_job,
    job_status,
    retry_executor,
)

ROOT = Path(__file__).resolve().parents[5]


def build_recovery(session_factory, clock, uuids):
    manager = ProductionLeaseManager(
        SQLAlchemyLeaseRepository(session_factory),
        clock=clock,
        lease_duration=timedelta(seconds=5),
    )
    recovery = ProductionRecoveryService(
        RuntimeStateReader(session_factory),
        ImmediateRuntimeDecisionPersister(
            OrchestrationDecisionStore(session_factory, clock=clock)
        ),
        manager,
        ImmediateRuntimeBlockingExecutor(),
        clock=clock,
        uuid_factory=uuids,
    )
    return recovery, manager


@pytest.mark.asyncio
async def test_recovery_runs_without_worker_and_respects_retry_time(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(2)
    job_id = UUID("10000000-0000-4000-8000-000000000391")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(
        session_factory,
        clock,
        uuids,
        owner_id="recovery-setup",
        executor=retry_executor(clock, uuids),
    )
    await worker.run_once()
    await worker.run_once()
    recovery, _ = build_recovery(session_factory, clock, uuids)
    assert await recovery.requeue_due_retries() == ()
    assert job_status(session_factory, job_id) is ProductionJobStatus.WAITING_FOR_RETRY
    clock.advance(2)
    assert await recovery.requeue_due_retries() == (job_id,)
    assert job_status(session_factory, job_id) is ProductionJobStatus.QUEUED


@pytest.mark.asyncio
async def test_expired_lease_inspection_does_not_change_job(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(1)
    job_id = UUID("10000000-0000-4000-8000-000000000392")
    await enqueue_job(session_factory, clock, uuids, job_id)
    recovery, manager = build_recovery(session_factory, clock, uuids)
    assert manager.acquire_next(owner_id="expired-owner", statuses={ProductionJobStatus.QUEUED})
    clock.advance(6)
    assert recovery.inspect_expired_leases() == (job_id,)
    assert job_status(session_factory, job_id) is ProductionJobStatus.QUEUED


def test_recovery_has_no_worker_or_handler_dependency() -> None:
    source = (
        ROOT / "backend" / "src" / "production" / "runtime" / "recovery.py"
    ).read_text(encoding="utf-8")
    assert "runtime.worker" not in source
    assert "runtime.handlers" not in source
    assert "ProductionExecutor" not in source


@pytest.mark.asyncio
async def test_requeue_concurrency_is_expected_but_integrity_errors_propagate(
    runtime_database,
) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(1)
    job_id = UUID("10000000-0000-4000-8000-000000000393")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(
        session_factory,
        clock,
        uuids,
        owner_id="recovery-race-setup",
        executor=retry_executor(clock, uuids),
    )
    await worker.run_once()
    await worker.run_once()
    clock.advance(2)
    reader = RuntimeStateReader(session_factory)
    _, manager = build_recovery(session_factory, clock, uuids)

    class FailingPersister:
        def __init__(self, error) -> None:
            self.error = error

        async def persist_decision(self, **kwargs):
            raise self.error

    expected_race = ProductionRecoveryService(
        reader,
        FailingPersister(ProductionConcurrencyError("won elsewhere")),
        manager,
        ImmediateRuntimeBlockingExecutor(),
        clock=clock,
        uuid_factory=uuids,
    )
    assert await expected_race.requeue_due_retries() == ()

    integrity_failure = ProductionRecoveryService(
        reader,
        FailingPersister(ProductionRecordIntegrityError("corrupt")),
        manager,
        ImmediateRuntimeBlockingExecutor(),
        clock=clock,
        uuid_factory=uuids,
    )
    with pytest.raises(ProductionRecordIntegrityError, match="corrupt"):
        await integrity_failure.requeue_due_retries()
