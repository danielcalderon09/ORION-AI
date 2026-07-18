import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.runtime import ProductionLease, ProductionWorker, WorkerRunResult

NOW = datetime(2026, 7, 18, 11, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000371")


class FakeRecovery:
    def __init__(self) -> None:
        self.calls = 0

    async def requeue_due_retries(self) -> tuple[UUID, ...]:
        self.calls += 1
        return ()


class FakeLeaseManager:
    def __init__(self, lease: ProductionLease | None) -> None:
        self.lease = lease
        self.released: list[tuple[UUID, str]] = []

    def acquire_next(self, *, owner_id, statuses):
        assert ProductionJobStatus.QUEUED in statuses
        lease, self.lease = self.lease, None
        return lease

    def release(self, *, job_id: UUID, owner_id: str) -> bool:
        self.released.append((job_id, owner_id))
        return True


class FakeProcessor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[ProductionLease, str]] = []

    async def process(self, *, lease: ProductionLease, owner_id: str) -> WorkerRunResult:
        self.calls.append((lease, owner_id))
        if self.fail:
            raise RuntimeError("processor failed")
        return WorkerRunResult(processed=True, job_id=lease.job_id)


def make_lease() -> ProductionLease:
    return ProductionLease(
        job_id=JOB_ID,
        owner_id="worker-facade",
        heartbeat_at=NOW,
        lease_until=NOW + timedelta(seconds=10),
        version=1,
    )


@pytest.mark.asyncio
async def test_worker_delegates_claim_and_always_releases() -> None:
    recovery = FakeRecovery()
    leases = FakeLeaseManager(make_lease())
    processor = FakeProcessor()
    worker = ProductionWorker(
        owner_id="worker-facade",
        lease_manager=leases,
        recovery=recovery,
        processor=processor,
    )
    result = await worker.run_once()
    assert result.processed is True
    assert recovery.calls == 1
    assert len(processor.calls) == 1
    assert leases.released == [(JOB_ID, "worker-facade")]


@pytest.mark.asyncio
async def test_worker_releases_lease_when_processor_raises() -> None:
    leases = FakeLeaseManager(make_lease())
    worker = ProductionWorker(
        owner_id="worker-facade",
        lease_manager=leases,
        recovery=FakeRecovery(),
        processor=FakeProcessor(fail=True),
    )
    with pytest.raises(RuntimeError, match="processor failed"):
        await worker.run_once()
    assert leases.released == [(JOB_ID, "worker-facade")]


@pytest.mark.asyncio
async def test_worker_run_until_idle_and_run_forever_stop() -> None:
    recovery = FakeRecovery()
    worker = ProductionWorker(
        owner_id="worker-facade",
        lease_manager=FakeLeaseManager(None),
        recovery=recovery,
        processor=FakeProcessor(),
    )
    results = await worker.run_until_idle(max_cycles=2)
    assert len(results) == 1 and results[0].processed is False
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        worker.run_forever(stop_event=stop_event, poll_interval_seconds=0.001)
    )
    await asyncio.sleep(0)
    stop_event.set()
    await task
    assert recovery.calls >= 2
