from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, update

from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.models import (
    ArtifactRecord,
    ProductionJobRecord,
    ProductionLeaseRecord,
    StageCommandRecord,
    StageResultRecord,
)
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.planning.artifact_writer import InMemoryPlanningArtifactWriter
from backend.src.production.planning.exceptions import PlanningProviderTimeoutError
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.runtime import (
    ProductionExecutor,
    ProductionLeaseManager,
    SQLAlchemyLeaseRepository,
    StageHandlerRegistry,
)
from backend.src.production.runtime.handlers import (
    AssetHandler,
    ClipHandoffHandler,
    MusicHandler,
    NarrationHandler,
    PlanningHandler,
    ScenePlanningHandler,
    ScriptHandler,
    SubtitleHandler,
    TimelineHandler,
    ValidationHandler,
    VisualAssetPlanningHandler,
)
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.tests.unit.production.runtime.conftest import (
    MutableClock,
    TestRenderBoundaryHandler,
    TestVideoClipBoundaryHandler,
    UUIDSequence,
    build_worker,
)


async def enqueue_job(session_factory, clock, uuids, job_id: UUID) -> None:
    job = ProductionJob(
        job_id=job_id,
        prompt="Run the simulated local pipeline",
        created_at=clock(),
        updated_at=clock(),
        configuration_snapshot={"runtime": "simulated"},
    )
    orchestrator = ProductionOrchestrator(clock=clock, uuid_factory=uuids)
    decision = orchestrator.decide(job, PipelineConfiguration(), next_sequence_number=0)
    await OrchestrationDecisionStore(session_factory, clock=clock).persist_decision(
        previous_job=job,
        decision=decision,
    )


def job_status(session_factory, job_id: UUID) -> ProductionJobStatus:
    with session_factory() as session:
        record = session.get(ProductionJobRecord, str(job_id))
        assert record is not None
        return ProductionJobStatus(record.status)


@pytest.mark.asyncio
async def test_worker_processes_one_complete_job(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence()
    job_id = UUID("10000000-0000-4000-8000-000000000311")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(session_factory, clock, uuids, owner_id="worker-complete")
    cycles = await worker.run_until_idle(max_cycles=20)
    assert job_status(session_factory, job_id) is ProductionJobStatus.COMPLETED
    assert cycles[-1].processed is False
    with session_factory() as session:
        assert session.scalar(select(func.count(StageResultRecord.command_id))) == 12
        assert session.scalar(select(func.count(ArtifactRecord.artifact_id))) == 12
        assert session.scalar(select(func.count(ProductionLeaseRecord.job_id))) == 0


@pytest.mark.asyncio
async def test_worker_processes_multiple_jobs(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(7)
    job_ids = (
        UUID("10000000-0000-4000-8000-000000000312"),
        UUID("10000000-0000-4000-8000-000000000313"),
    )
    for job_id in job_ids:
        await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(session_factory, clock, uuids, owner_id="worker-many")
    await worker.run_until_idle(max_cycles=40)
    assert [job_status(session_factory, item) for item in job_ids] == [
        ProductionJobStatus.COMPLETED,
        ProductionJobStatus.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_worker_pipeline_with_clip_handoff_completes(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(9)
    job_id = UUID("10000000-0000-4000-8000-000000000318")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(
        session_factory,
        clock,
        uuids,
        owner_id="worker-with-clips",
        generate_clips=True,
    )
    await worker.run_until_idle(max_cycles=20)
    assert job_status(session_factory, job_id) is ProductionJobStatus.COMPLETED
    with session_factory() as session:
        assert session.scalar(select(func.count(StageResultRecord.command_id))) == 14


def retry_executor(clock, uuids) -> ProductionExecutor:
    common = {"clock": clock, "uuid_factory": uuids}

    class RetryOncePlanningProvider(SimulatedPlanningProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def generate_plan(self, request):
            self.calls += 1
            if self.calls == 1:
                raise PlanningProviderTimeoutError("simulated timeout")
            return await super().generate_plan(request)

    return ProductionExecutor(
        StageHandlerRegistry(
            (
                PlanningHandler(
                    **common,
                    provider=RetryOncePlanningProvider(),
                    artifact_writer=InMemoryPlanningArtifactWriter(),
                ),
                ScriptHandler(**common),
                ScenePlanningHandler(**common),
                VisualAssetPlanningHandler(**common),
                AssetHandler(**common),
                TestVideoClipBoundaryHandler(**common),
                NarrationHandler(**common),
                MusicHandler(**common),
                SubtitleHandler(**common),
                TimelineHandler(**common),
                TestRenderBoundaryHandler(**common),
                ValidationHandler(**common),
                ClipHandoffHandler(**common),
            )
        )
    )


@pytest.mark.asyncio
async def test_transient_failure_requeues_when_retry_is_due(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(6)
    job_id = UUID("10000000-0000-4000-8000-000000000314")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(
        session_factory,
        clock,
        uuids,
        owner_id="worker-retry",
        executor=retry_executor(clock, uuids),
    )
    await worker.run_once()
    await worker.run_once()
    assert job_status(session_factory, job_id) is ProductionJobStatus.WAITING_FOR_RETRY
    assert (await worker.run_once()).processed is False
    clock.advance(2)
    await worker.run_until_idle(max_cycles=20)
    assert job_status(session_factory, job_id) is ProductionJobStatus.COMPLETED
    with session_factory() as session:
        attempts = list(
            session.scalars(
                select(StageCommandRecord.attempt_number)
                .where(
                    StageCommandRecord.job_id == str(job_id),
                    StageCommandRecord.stage == ProductionStage.PLANNING.value,
                )
                .order_by(StageCommandRecord.attempt_number)
            )
        )
        assert attempts == [1, 2]


@pytest.mark.asyncio
async def test_cancel_requested_finishes_current_stage_without_new_command(
    runtime_database,
) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(5)
    job_id = UUID("10000000-0000-4000-8000-000000000315")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(session_factory, clock, uuids, owner_id="worker-cancel")
    await worker.run_once()
    with session_factory() as session:
        session.execute(
            update(ProductionJobRecord)
            .where(ProductionJobRecord.job_id == str(job_id))
            .values(status=ProductionJobStatus.CANCEL_REQUESTED.value, updated_at=clock())
        )
        session.commit()
    await worker.run_once()
    assert job_status(session_factory, job_id) is ProductionJobStatus.CANCELLED
    with session_factory() as session:
        assert session.scalar(select(func.count(StageResultRecord.command_id))) == 1
        assert session.scalar(select(func.count(ArtifactRecord.artifact_id))) == 1
        assert session.scalar(select(func.count(StageCommandRecord.command_id))) == 1


@pytest.mark.asyncio
async def test_cancellation_during_stage_persists_result_before_cancelling(
    runtime_database,
) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(3)
    job_id = UUID("10000000-0000-4000-8000-000000000317")
    await enqueue_job(session_factory, clock, uuids, job_id)

    class CancellingPlanningHandler(PlanningHandler):
        async def execute(self, command, context) -> StageExecutionOutput:
            with session_factory() as session:
                session.execute(
                    update(ProductionJobRecord)
                    .where(ProductionJobRecord.job_id == str(command.job_id))
                    .values(
                        status=ProductionJobStatus.CANCEL_REQUESTED.value,
                        updated_at=clock(),
                    )
                )
                session.commit()
            return await super().execute(command, context)

    common = {"clock": clock, "uuid_factory": uuids}
    planning_common = {
        **common,
        "provider": SimulatedPlanningProvider(),
        "artifact_writer": InMemoryPlanningArtifactWriter(),
    }
    executor = ProductionExecutor(
        StageHandlerRegistry(
            (
                CancellingPlanningHandler(**planning_common),
                ScriptHandler(**common),
                ScenePlanningHandler(**common),
                AssetHandler(**common),
                NarrationHandler(**common),
                MusicHandler(**common),
                SubtitleHandler(**common),
                TimelineHandler(**common),
                TestRenderBoundaryHandler(**common),
                ValidationHandler(**common),
                ClipHandoffHandler(**common),
            )
        )
    )
    worker = build_worker(
        session_factory,
        clock,
        uuids,
        owner_id="worker-cancel-active",
        executor=executor,
    )
    await worker.run_once()
    await worker.run_once()
    assert job_status(session_factory, job_id) is ProductionJobStatus.CANCELLED
    with session_factory() as session:
        assert session.scalar(select(func.count(StageResultRecord.command_id))) == 1
        assert session.scalar(select(func.count(ArtifactRecord.artifact_id))) == 1


@pytest.mark.asyncio
async def test_new_worker_recovers_expired_running_lease(runtime_database) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(4)
    job_id = UUID("10000000-0000-4000-8000-000000000316")
    await enqueue_job(session_factory, clock, uuids, job_id)
    first_worker = build_worker(session_factory, clock, uuids, owner_id="worker-before-restart")
    await first_worker.run_once()
    abandoned = ProductionLeaseManager(
        SQLAlchemyLeaseRepository(session_factory),
        clock=clock,
        lease_duration=timedelta(seconds=5),
    )
    assert abandoned.acquire_next(owner_id="dead-worker", statuses={ProductionJobStatus.RUNNING})
    clock.advance(6)
    replacement = build_worker(session_factory, clock, uuids, owner_id="worker-after-restart")
    result = await replacement.run_once()
    assert result.processed is True
    assert result.updated_stage is ProductionStage.SCRIPTING
    assert job_status(session_factory, job_id) is ProductionJobStatus.RUNNING
