"""Composition root for Production HTTP use cases and simulated runtime."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.application.services.production_jobs import (
    CancelProductionJobService,
    CreateProductionJobService,
    GetProductionJobService,
    ListProductionArtifactsService,
    ListProductionEventsService,
    ListProductionJobsService,
    RetryProductionJobService,
)
from backend.src.production.infrastructure.persistence.query_repositories import (
    SQLAlchemyProductionArtifactQueryRepository,
    SQLAlchemyProductionEventQueryRepository,
    SQLAlchemyProductionJobQueryRepository,
)
from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    create_production_session_factory,
)
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.planning.artifact_writer import LocalPlanningArtifactWriter
from backend.src.production.planning.exceptions import (
    PlanningProviderConfigurationError,
)
from backend.src.production.planning.ports import PlanningProvider
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.runtime import (
    ClaimedJobProcessor,
    ProductionExecutor,
    ProductionHeartbeat,
    ProductionRecoveryService,
    ProductionWorker,
    RuntimeStateReader,
    StageContextFactory,
    create_handler_registry,
)
from backend.src.production.runtime.blocking_executor import (
    ThreadedRuntimeBlockingExecutor,
)
from backend.src.production.runtime.decision_persister import (
    ThreadedRuntimeDecisionPersister,
)
from backend.src.production.runtime.handlers import PlanningHandler
from backend.src.production.runtime.leases import (
    ProductionLeaseManager,
    SQLAlchemyLeaseRepository,
)


@dataclass(frozen=True, slots=True)
class ProductionContainer:
    engine: Engine
    create_job: CreateProductionJobService
    get_job: GetProductionJobService
    list_jobs: ListProductionJobsService
    cancel_job: CancelProductionJobService
    retry_job: RetryProductionJobService
    list_events: ListProductionEventsService
    list_artifacts: ListProductionArtifactsService
    recovery: ProductionRecoveryService
    worker: ProductionWorker
    planning_provider: PlanningProvider

    def shutdown(self) -> None:
        self.engine.dispose()

    async def aclose(self) -> None:
        try:
            await self.planning_provider.close()
        finally:
            self.engine.dispose()


def build_production_container(settings: Settings) -> ProductionContainer:
    def clock() -> datetime:
        return datetime.now(UTC)
    engine = create_production_engine(
        settings.production_database_url,
        echo=settings.ORION_DATABASE_ECHO,
    )
    sessions = create_production_session_factory(engine)
    blocking = ThreadedRuntimeBlockingExecutor()
    jobs = SQLAlchemyProductionJobQueryRepository(sessions)
    events = SQLAlchemyProductionEventQueryRepository(sessions)
    artifacts = SQLAlchemyProductionArtifactQueryRepository(sessions)
    orchestrator = ProductionOrchestrator(clock=clock, uuid_factory=uuid4)
    store = OrchestrationDecisionStore(sessions, clock=clock)
    persister = ThreadedRuntimeDecisionPersister(store)
    try:
        planning_provider = _build_planning_provider(settings)
    except Exception:
        engine.dispose()
        raise
    planning_handler = PlanningHandler(
        provider=planning_provider,
        artifact_writer=LocalPlanningArtifactWriter(settings.PROJECTS_DIR),
        clock=clock,
        uuid_factory=uuid4,
    )

    leases = ProductionLeaseManager(
        SQLAlchemyLeaseRepository(sessions),
        clock=clock,
        lease_duration=timedelta(
            seconds=settings.ORION_PRODUCTION_LEASE_DURATION_SECONDS
        ),
    )
    recovery = ProductionRecoveryService(
        RuntimeStateReader(sessions),
        persister,
        leases,
        blocking,
        clock=clock,
        uuid_factory=uuid4,
    )
    processor = ClaimedJobProcessor(
        state_reader=RuntimeStateReader(sessions),
        blocking_executor=blocking,
        orchestrator=orchestrator,
        configuration=PipelineConfiguration(),
        decision_store=persister,
        heartbeat=ProductionHeartbeat(
            leases,
            interval=timedelta(
                seconds=settings.ORION_PRODUCTION_HEARTBEAT_INTERVAL_SECONDS
            ),
        ),
        executor=ProductionExecutor(
            create_handler_registry(
                planning_handler=planning_handler,
                clock=clock,
                uuid_factory=uuid4,
            )
        ),
        context_factory=StageContextFactory(),
    )
    owner_id = settings.ORION_PRODUCTION_WORKER_OWNER_ID or f"orion-{uuid4()}"
    worker = ProductionWorker(
        owner_id=owner_id,
        lease_manager=leases,
        recovery=recovery,
        processor=processor,
    )
    return ProductionContainer(
        engine=engine,
        create_job=CreateProductionJobService(
            query=jobs,
            blocking_executor=blocking,
            persister=persister,
            orchestrator=orchestrator,
            clock=clock,
            uuid_factory=uuid4,
        ),
        get_job=GetProductionJobService(jobs, blocking),
        list_jobs=ListProductionJobsService(jobs, blocking),
        cancel_job=CancelProductionJobService(
            query=jobs,
            events=events,
            blocking=blocking,
            persister=persister,
            clock=clock,
            uuid_factory=uuid4,
        ),
        retry_job=RetryProductionJobService(
            query=jobs,
            events=events,
            blocking=blocking,
            persister=persister,
            clock=clock,
            uuid_factory=uuid4,
        ),
        list_events=ListProductionEventsService(jobs, events, blocking),
        list_artifacts=ListProductionArtifactsService(jobs, artifacts, blocking),
        recovery=recovery,
        worker=worker,
        planning_provider=planning_provider,
    )


def _build_planning_provider(settings: Settings) -> PlanningProvider:
    provider_name = settings.ORION_PLANNING_PROVIDER.strip().lower()
    if provider_name == "simulated":
        return SimulatedPlanningProvider()
    if provider_name != "openai":
        raise PlanningProviderConfigurationError(
            f"unsupported planning provider: {provider_name!r}"
        )
    if settings.ORION_PLANNING_API_KEY is None:
        raise PlanningProviderConfigurationError("planning provider credential is missing")
    from backend.src.production.planning.providers.openai_provider import (
        OpenAIPlanningProvider,
    )

    return OpenAIPlanningProvider(
        api_key=settings.ORION_PLANNING_API_KEY.get_secret_value(),
        model=settings.ORION_PLANNING_MODEL,
        prompt_builder=PlanningPromptBuilder(),
        base_url=settings.ORION_PLANNING_BASE_URL,
        timeout_seconds=settings.ORION_PLANNING_TIMEOUT_SECONDS,
        max_transport_attempts=settings.ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS,
        retry_base_delay_seconds=settings.ORION_PLANNING_RETRY_BASE_DELAY_SECONDS,
        max_output_tokens=settings.ORION_PLANNING_MAX_OUTPUT_TOKENS,
        temperature=settings.ORION_PLANNING_TEMPERATURE,
    )
