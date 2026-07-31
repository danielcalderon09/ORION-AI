"""Always-on pure orchestration proof; it does not claim real rendering."""

from uuid import UUID

import pytest

from backend.src.production.application.events import ProductionStageSucceeded
from backend.src.production.application.orchestration import ProductionOrchestrator, StageRegistry
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.application.services.models import CreateProductionJobCommand
from backend.src.production.application.services.production_jobs import (
    CreateProductionJobService,
    GetProductionJobService,
    ListProductionEventsService,
)
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionJobStatus,
    ProductionStage,
)
from backend.src.production.infrastructure.persistence.models import ProductionBase
from backend.src.production.infrastructure.persistence.query_repositories import (
    SQLAlchemyProductionEventQueryRepository,
    SQLAlchemyProductionJobQueryRepository,
)
from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    create_production_session_factory,
    sqlite_url_from_path,
)
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.local_mvp import local_mvp_profile
from backend.src.production.runtime import (
    ImmediateRuntimeBlockingExecutor,
    ImmediateRuntimeDecisionPersister,
    ProductionExecutor,
)
from backend.src.production.runtime.handler_factory import create_simulated_handler_registry
from backend.src.production.runtime.job_dispatcher import StageHandlerRegistry
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.tests.unit.production.runtime.conftest import (
    MutableClock,
    TestVideoClipBoundaryHandler,
    UUIDSequence,
    build_worker,
)


@pytest.fixture
def pure_runtime_database(tmp_path):
    engine = create_production_engine(sqlite_url_from_path(tmp_path / "pure-e2e.db"))
    ProductionBase.metadata.create_all(engine)
    sessions = create_production_session_factory(engine)
    try:
        yield engine, sessions
    finally:
        engine.dispose()


class PureRenderHandler:
    supported_stages = frozenset({ProductionStage.RENDERING_LONG_FORM})

    def __init__(self, *, clock: MutableClock, uuids: UUIDSequence) -> None:
        self.clock = clock
        self.uuids = uuids
        self.output_ids: tuple[UUID, ...] = ()
        self.render_id: UUID | None = None

    async def execute(self, command, context) -> StageExecutionOutput:
        del context
        types = (
            ArtifactType.LOCAL_RENDER_REQUEST,
            ArtifactType.FFMPEG_EXECUTION_PLAN,
            ArtifactType.RENDER_EXECUTION_MANIFEST,
            ArtifactType.LONG_FORM_RENDER,
        )
        artifacts = tuple(
            Artifact(
                artifact_id=self.uuids(),
                job_id=command.job_id,
                artifact_type=kind,
                relative_path=f"pure/{command.job_id}/{kind.value}.bin",
                mime_type="video/mp4"
                if kind is ArtifactType.LONG_FORM_RENDER
                else "application/json",
                status=ArtifactStatus.READY,
                size_bytes=1,
                sha256=f"{index + 1:064x}",
                provider="pure-orchestration-double",
            )
            for index, kind in enumerate(types)
        )
        self.output_ids = tuple(item.artifact_id for item in artifacts)
        self.render_id = artifacts[-1].artifact_id
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=self.clock(),
                finished_at=self.clock(),
                progress_percent=100,
                output_artifact_ids=self.output_ids,
                metadata={"pure_test_double": True},
            ),
            artifacts=artifacts,
        )


class PureValidationHandler:
    supported_stages = frozenset({ProductionStage.VALIDATING_RENDER})

    def __init__(self, *, clock: MutableClock, uuids: UUIDSequence, render: PureRenderHandler):
        self.clock = clock
        self.uuids = uuids
        self.render = render
        self.observed_inputs: tuple[UUID, ...] = ()

    async def execute(self, command, context) -> StageExecutionOutput:
        del context
        self.observed_inputs = command.input_artifact_ids
        assert self.observed_inputs == self.render.output_ids
        assert self.render.render_id is not None
        artifact = Artifact(
            artifact_id=self.uuids(),
            job_id=command.job_id,
            artifact_type=ArtifactType.FINAL_RENDER_VALIDATION,
            relative_path=f"pure/{command.job_id}/final-render-validation.json",
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=1,
            sha256="f" * 64,
            provider="pure-orchestration-double",
        )
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=self.clock(),
                finished_at=self.clock(),
                progress_percent=100,
                output_artifact_ids=(artifact.artifact_id,),
                metadata={"render_artifact_id": str(self.render.render_id)},
            ),
            artifacts=(artifact,),
        )


@pytest.mark.asyncio
async def test_pure_e2e_uses_normal_creation_registry_and_canonical_orchestrator(
    pure_runtime_database,
) -> None:
    _, sessions = pure_runtime_database
    clock, uuids = MutableClock(), UUIDSequence(42)
    jobs = SQLAlchemyProductionJobQueryRepository(sessions)
    events = SQLAlchemyProductionEventQueryRepository(sessions)
    blocking = ImmediateRuntimeBlockingExecutor()
    orchestrator = ProductionOrchestrator(clock=clock, uuid_factory=uuids)
    persister = ImmediateRuntimeDecisionPersister(OrchestrationDecisionStore(sessions, clock=clock))
    create = CreateProductionJobService(
        query=jobs,
        blocking_executor=blocking,
        persister=persister,
        orchestrator=orchestrator,
        clock=clock,
        uuid_factory=uuids,
    )
    command = CreateProductionJobCommand(
        prompt="Explica en un video corto tres curiosidades sobre Marte.",
        configuration=local_mvp_profile(),
        client_request_id="pure-local-e2e-mars",
        metadata={"mode": "local_simulated_e2e", "proof": "orchestration_only"},
    )
    created = await create.execute(command)
    replayed = await create.execute(command)
    assert replayed.job.job_id == created.job.job_id

    render = PureRenderHandler(clock=clock, uuids=uuids)
    validation = PureValidationHandler(clock=clock, uuids=uuids, render=render)
    registry = create_simulated_handler_registry(
        clock=clock,
        uuid_factory=uuids,
        video_clip_generation_handler=TestVideoClipBoundaryHandler(clock=clock, uuid_factory=uuids),
        render_handler=render,
        final_render_validation_handler=validation,
    )
    assert isinstance(registry, StageHandlerRegistry)
    worker = build_worker(
        sessions,
        clock,
        uuids,
        owner_id="pure-local-e2e",
        executor=ProductionExecutor(registry),
    )
    await worker.run_until_idle(max_cycles=20)

    final = await GetProductionJobService(jobs, blocking).execute(created.job.job_id)
    assert final.job.status is ProductionJobStatus.COMPLETED
    assert final.job.current_stage is ProductionStage.COMPLETED
    assert final.job.long_form_artifact_id == render.render_id
    assert validation.observed_inputs == render.output_ids
    event_page = await ListProductionEventsService(jobs, events, blocking).execute(
        created.job.job_id
    )
    succeeded = tuple(
        item.stage for item in event_page.items if isinstance(item, ProductionStageSucceeded)
    )
    assert succeeded == StageRegistry.active_stages(generate_clips_after_render=False)[1:-1]
