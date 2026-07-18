"""Temporary, deterministic runtime test composition."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.infrastructure.persistence.models import ProductionBase
from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    create_production_session_factory,
    sqlite_url_from_path,
)
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.runtime import (
    ProductionExecutor,
    ProductionHeartbeat,
    ProductionLeaseManager,
    ProductionRecoveryService,
    ProductionWorker,
    create_simulated_handler_registry,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 17, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class UUIDSequence:
    def __init__(self, prefix: int = 8) -> None:
        self._prefix = prefix
        self._value = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08d}-0000-4000-8000-{self._value:012d}")
        self._value += 1
        return value


@pytest.fixture
def runtime_database(tmp_path):
    engine = create_production_engine(sqlite_url_from_path(tmp_path / "runtime.db"))
    ProductionBase.metadata.create_all(engine)
    session_factory = create_production_session_factory(engine)
    try:
        yield engine, session_factory
    finally:
        engine.dispose()


def build_worker(
    session_factory,
    clock: MutableClock,
    uuids: UUIDSequence,
    *,
    owner_id: str,
    executor: ProductionExecutor | None = None,
    generate_clips: bool = False,
) -> ProductionWorker:
    store = OrchestrationDecisionStore(session_factory, clock=clock)
    leases = ProductionLeaseManager(
        session_factory,
        clock=clock,
        lease_duration=timedelta(seconds=10),
    )
    return ProductionWorker(
        session_factory,
        owner_id=owner_id,
        orchestrator=ProductionOrchestrator(clock=clock, uuid_factory=uuids),
        configuration=PipelineConfiguration(
            generate_clips_after_render=generate_clips,
            default_retry_after_seconds=1,
        ),
        decision_store=store,
        lease_manager=leases,
        heartbeat=ProductionHeartbeat(leases, interval=timedelta(seconds=1)),
        executor=executor
        or ProductionExecutor(
            create_simulated_handler_registry(clock=clock, uuid_factory=uuids)
        ),
        recovery=ProductionRecoveryService(
            session_factory,
            store,
            leases,
            clock=clock,
            uuid_factory=uuids,
        ),
        clock=clock,
    )
