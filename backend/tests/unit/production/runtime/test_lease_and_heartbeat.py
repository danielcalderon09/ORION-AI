from datetime import timedelta
from uuid import UUID

import pytest

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.repositories import (
    SQLAlchemyProductionJobRepository,
)
from backend.src.production.runtime import (
    ProductionHeartbeat,
    ProductionLeaseManager,
    ProductionLeaseOwnershipError,
)


async def add_queued_job(session_factory, clock, job_id: UUID) -> None:
    job = ProductionJob(
        job_id=job_id,
        prompt="Lease test",
        status=ProductionJobStatus.QUEUED,
        created_at=clock(),
        updated_at=clock(),
    )
    with session_factory() as session:
        await SQLAlchemyProductionJobRepository(session, clock=clock).add(job)
        session.commit()


@pytest.mark.asyncio
async def test_lease_is_unique_and_expired_lease_can_be_reclaimed(
    runtime_database,
) -> None:
    _, session_factory = runtime_database
    from backend.tests.unit.production.runtime.conftest import MutableClock

    clock = MutableClock()
    job_id = UUID("10000000-0000-4000-8000-000000000301")
    await add_queued_job(session_factory, clock, job_id)
    first = ProductionLeaseManager(
        session_factory, clock=clock, lease_duration=timedelta(seconds=5)
    )
    second = ProductionLeaseManager(
        session_factory, clock=clock, lease_duration=timedelta(seconds=5)
    )
    assert first.acquire_next(owner_id="worker-a", statuses={ProductionJobStatus.QUEUED})
    assert second.acquire_next(
        owner_id="worker-b", statuses={ProductionJobStatus.QUEUED}
    ) is None
    clock.advance(6)
    reclaimed = second.acquire_next(
        owner_id="worker-b", statuses={ProductionJobStatus.QUEUED}
    )
    assert reclaimed is not None
    assert reclaimed.owner_id == "worker-b"
    assert reclaimed.version == 2


@pytest.mark.asyncio
async def test_heartbeat_renews_only_owned_active_lease(runtime_database) -> None:
    _, session_factory = runtime_database
    from backend.tests.unit.production.runtime.conftest import MutableClock

    clock = MutableClock()
    job_id = UUID("10000000-0000-4000-8000-000000000302")
    await add_queued_job(session_factory, clock, job_id)
    manager = ProductionLeaseManager(
        session_factory, clock=clock, lease_duration=timedelta(seconds=5)
    )
    original = manager.acquire_next(
        owner_id="worker-a", statuses={ProductionJobStatus.QUEUED}
    )
    assert original is not None
    clock.advance(2)
    heartbeat = ProductionHeartbeat(manager, interval=timedelta(seconds=1))
    heartbeat.beat(job_id=job_id, owner_id="worker-a")
    renewed = manager.acquire_next(
        owner_id="worker-a", statuses={ProductionJobStatus.QUEUED}
    )
    assert renewed is not None
    assert renewed.lease_until > original.lease_until
    with pytest.raises(ProductionLeaseOwnershipError):
        heartbeat.beat(job_id=job_id, owner_id="worker-b")
