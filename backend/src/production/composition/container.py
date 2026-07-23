"""Composition root for Production HTTP use cases and simulated runtime."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit
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
from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.filesystem_store import (
    FilesystemBinaryAssetStore,
)
from backend.src.production.binary_assets.ports import (
    BinaryAssetReader,
    BinaryAssetStore,
    BinaryAssetWriter,
)
from backend.src.production.binary_assets.reconciliation import (
    FilesystemBinaryAssetReconciler,
)
from backend.src.production.binary_assets.validators import (
    AssetHashValidator,
    AssetMimeValidator,
    AssetSizeValidator,
    BinaryAssetIntegrityValidator,
)
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderConfigurationException,
)
from backend.src.production.image_acquisition.handler import ImageAcquisitionHandler
from backend.src.production.image_acquisition.manifest_writer import (
    LocalImageAcquisitionManifestWriter,
)
from backend.src.production.image_acquisition.ports import ImageAcquisitionProvider
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.providers import (
    SimulatedImageAcquisitionProvider,
)
from backend.src.production.image_acquisition.providers.availability import (
    ImageAcquisitionProviderFactory,
    load_openrouter_image_acquisition_provider,
)
from backend.src.production.infrastructure.durable_production_plan_reader import (
    DurableProductionPlanReader,
)
from backend.src.production.infrastructure.durable_production_scene_plan_reader import (
    DurableProductionScenePlanReader,
)
from backend.src.production.infrastructure.durable_production_script_reader import (
    DurableProductionScriptReader,
)
from backend.src.production.infrastructure.durable_production_visual_asset_plan_reader import (
    DurableProductionVisualAssetPlanReader,
)
from backend.src.production.infrastructure.persistence.planning_artifact_reader import (
    SQLAlchemyRegisteredPlanningArtifactReader,
)
from backend.src.production.infrastructure.persistence.production_plan_query_repository import (
    SQLAlchemyProductionPlanQueryRepository,
)
from backend.src.production.infrastructure.persistence.production_scene_plan_query_repository import (
    SQLAlchemyProductionScenePlanQueryRepository,
)
from backend.src.production.infrastructure.persistence.production_script_query_repository import (
    SQLAlchemyProductionScriptQueryRepository,
)
from backend.src.production.infrastructure.persistence.production_visual_asset_plan_query_repository import (
    SQLAlchemyProductionVisualAssetPlanQueryRepository,
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
from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalProductionArtifactReconciler,
)
from backend.src.production.planning.artifact_writer import LocalPlanningArtifactWriter
from backend.src.production.planning.exceptions import (
    PlanningProviderConfigurationError,
)
from backend.src.production.planning.ports import PlanningProvider
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.planning.providers.availability import (
    load_openrouter_planning_provider,
)
from backend.src.production.planning.reconciliation import PlanningArtifactReconciler
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
from backend.src.production.runtime.handlers import PlanningHandler, ScriptingHandler
from backend.src.production.runtime.leases import (
    ProductionLeaseManager,
    SQLAlchemyLeaseRepository,
)
from backend.src.production.scene_planning.artifact_writer import (
    LocalScenePlanningArtifactWriter,
)
from backend.src.production.scene_planning.exceptions import (
    ScenePlanningProviderConfigurationException,
)
from backend.src.production.scene_planning.handler import ScenePlanningHandler
from backend.src.production.scene_planning.ports import ScenePlanningProvider
from backend.src.production.scene_planning.prompt_builder import (
    ScenePlanningPromptBuilder,
)
from backend.src.production.scene_planning.providers import (
    SimulatedScenePlanningProvider,
)
from backend.src.production.scene_planning.providers.availability import (
    ScenePlanningProviderFactory,
    load_openrouter_scene_planning_provider,
)
from backend.src.production.scripting.artifact_writer import LocalScriptingArtifactWriter
from backend.src.production.scripting.exceptions import (
    ScriptingProviderConfigurationError,
)
from backend.src.production.scripting.ports import ScriptingProvider
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.src.production.scripting.providers.availability import (
    ScriptingProviderFactory,
    load_openrouter_scripting_provider,
)
from backend.src.production.visual_asset_planning.artifact_writer import (
    LocalVisualAssetPlanningArtifactWriter,
)
from backend.src.production.visual_asset_planning.exceptions import (
    VisualAssetPlanningProviderConfigurationException,
)
from backend.src.production.visual_asset_planning.handler import (
    VisualAssetPlanningHandler,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProvider,
)
from backend.src.production.visual_asset_planning.prompt_builder import (
    VisualAssetPlanningPromptBuilder,
)
from backend.src.production.visual_asset_planning.providers import (
    SimulatedVisualAssetPlanningProvider,
)
from backend.src.production.visual_asset_planning.providers.availability import (
    VisualAssetPlanningProviderFactory,
    load_openrouter_visual_asset_planning_provider,
)


class AsyncClosable(Protocol):
    async def close(self) -> None: ...


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
    scripting_provider: ScriptingProvider
    scene_planning_provider: ScenePlanningProvider
    visual_asset_planning_provider: VisualAssetPlanningProvider
    image_acquisition_provider: ImageAcquisitionProvider
    binary_asset_configuration: AssetStorageConfiguration
    binary_asset_store: BinaryAssetStore
    binary_asset_writer: BinaryAssetWriter
    binary_asset_reader: BinaryAssetReader
    binary_asset_integrity_validator: BinaryAssetIntegrityValidator
    binary_asset_reconciler: FilesystemBinaryAssetReconciler
    planning_artifact_reconciler: PlanningArtifactReconciler
    async_resources: tuple[AsyncClosable, ...]

    def shutdown(self) -> None:
        self.engine.dispose()

    async def aclose(self) -> None:
        first_error: Exception | None = None
        try:
            for resource in self.async_resources:
                try:
                    await resource.close()
                except Exception as exc:
                    first_error = first_error or exc
        finally:
            self.engine.dispose()
        if first_error is not None:
            raise first_error


def build_production_container(settings: Settings) -> ProductionContainer:
    def clock() -> datetime:
        return datetime.now(UTC)
    scripting_factory = _resolve_scripting_provider_factory(settings)
    scene_planning_factory = _resolve_scene_planning_provider_factory(settings)
    visual_asset_planning_factory = (
        _resolve_visual_asset_planning_provider_factory(settings)
    )
    image_acquisition_factory = _resolve_image_acquisition_provider_factory(settings)
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
        scripting_provider = _build_scripting_provider(
            settings,
            openrouter_factory=scripting_factory,
        )
        scene_planning_provider = _build_scene_planning_provider(
            settings,
            openrouter_factory=scene_planning_factory,
        )
        visual_asset_planning_provider = _build_visual_asset_planning_provider(
            settings,
            openrouter_factory=visual_asset_planning_factory,
        )
        image_acquisition_provider = _build_image_acquisition_provider(
            settings,
            openrouter_factory=image_acquisition_factory,
        )
    except Exception:
        engine.dispose()
        raise
    planning_handler = PlanningHandler(
        provider=planning_provider,
        artifact_writer=LocalPlanningArtifactWriter(settings.PROJECTS_DIR),
        clock=clock,
        uuid_factory=uuid4,
    )
    scripting_handler = ScriptingHandler(
        plan_reader=DurableProductionPlanReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionPlanQueryRepository(sessions),
            max_plan_bytes=settings.ORION_SCRIPTING_MAX_PLAN_BYTES,
        ),
        provider=scripting_provider,
        artifact_writer=LocalScriptingArtifactWriter(
            settings.PROJECTS_DIR,
            max_script_bytes=settings.ORION_SCRIPTING_MAX_SCRIPT_BYTES,
        ),
        clock=clock,
        uuid_factory=uuid4,
    )
    scene_planning_handler = ScenePlanningHandler(
        script_reader=DurableProductionScriptReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScriptQueryRepository(sessions),
            max_script_bytes=settings.ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES,
        ),
        provider=scene_planning_provider,
        artifact_writer=LocalScenePlanningArtifactWriter(
            settings.PROJECTS_DIR,
            max_scene_plan_bytes=settings.ORION_SCENE_PLANNING_MAX_PLAN_BYTES,
        ),
        clock=clock,
        uuid_factory=uuid4,
    )
    visual_asset_planning_handler = VisualAssetPlanningHandler(
        scene_plan_reader=DurableProductionScenePlanReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScenePlanQueryRepository(sessions),
            max_scene_plan_bytes=(
                settings.ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES
            ),
        ),
        provider=visual_asset_planning_provider,
        artifact_writer=LocalVisualAssetPlanningArtifactWriter(
            settings.PROJECTS_DIR,
            max_artifact_bytes=(
                settings.ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES
            ),
        ),
        clock=clock,
        uuid_factory=uuid4,
    )
    binary_asset_configuration = AssetStorageConfiguration(
        workspace=settings.PROJECTS_DIR,
        max_asset_size=settings.ORION_BINARY_ASSET_MAX_SIZE_BYTES,
        allowed_mime_types=frozenset(
            settings.ORION_BINARY_ASSET_ALLOWED_MIME_TYPES
        ),
        allowed_extensions=frozenset(
            settings.ORION_BINARY_ASSET_ALLOWED_EXTENSIONS
        ),
    )
    asset_mime_validator = AssetMimeValidator(binary_asset_configuration)
    asset_hash_validator = AssetHashValidator()
    asset_size_validator = AssetSizeValidator(binary_asset_configuration)
    binary_asset_integrity_validator = BinaryAssetIntegrityValidator(
        mime_validator=asset_mime_validator,
        hash_validator=asset_hash_validator,
        size_validator=asset_size_validator,
    )
    filesystem_binary_asset_store = FilesystemBinaryAssetStore(
        configuration=binary_asset_configuration,
        integrity_validator=binary_asset_integrity_validator,
        clock=clock,
    )
    image_prompt_builder = ImageGenerationPromptBuilder(
        max_prompt_bytes=settings.ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES,
    )
    image_acquisition_handler = ImageAcquisitionHandler(
        plan_reader=DurableProductionVisualAssetPlanReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionVisualAssetPlanQueryRepository(sessions),
            max_plan_bytes=settings.ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES,
        ),
        provider=image_acquisition_provider,
        manifest_writer=LocalImageAcquisitionManifestWriter(
            settings.PROJECTS_DIR,
            max_manifest_bytes=settings.ORION_IMAGE_ACQUISITION_MAX_MANIFEST_BYTES,
        ),
        binary_reader=filesystem_binary_asset_store,
        binary_writer=filesystem_binary_asset_store,
        configuration=ImageAcquisitionConfiguration(
            output_format=settings.ORION_IMAGE_ACQUISITION_OUTPUT_FORMAT,
            quality=settings.ORION_IMAGE_ACQUISITION_QUALITY,
        ),
        provider_name=settings.ORION_IMAGE_ACQUISITION_PROVIDER.strip().lower(),
        requested_model=(
            settings.ORION_IMAGE_ACQUISITION_MODEL.strip() or None
        ),
        prompt_builder=image_prompt_builder,
        clock=clock,
    )
    binary_asset_reconciler = FilesystemBinaryAssetReconciler(
        configuration=binary_asset_configuration,
        store=filesystem_binary_asset_store,
    )
    planning_artifact_reconciler = LocalProductionArtifactReconciler(
        workspace_root=settings.PROJECTS_DIR,
        registered_reader=SQLAlchemyRegisteredPlanningArtifactReader(
            sessions,
            artifact_types=frozenset(
                {
                    ArtifactType.PRODUCTION_PLAN,
                    ArtifactType.PRODUCTION_SCRIPT,
                    ArtifactType.PRODUCTION_SCENE_PLAN,
                    ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN,
                    ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST,
                }
            ),
        ),
        minimum_age_seconds=settings.ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS,
        action=settings.ORION_PLANNING_ORPHAN_ACTION,
        quarantine_relative_path=settings.ORION_PLANNING_QUARANTINE_DIR,
        clock=clock,
        binary_reconciler=binary_asset_reconciler,
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
                scripting_handler=scripting_handler,
                scene_planning_handler=scene_planning_handler,
                visual_asset_planning_handler=visual_asset_planning_handler,
                image_acquisition_handler=image_acquisition_handler,
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
        scripting_provider=scripting_provider,
        scene_planning_provider=scene_planning_provider,
        visual_asset_planning_provider=visual_asset_planning_provider,
        image_acquisition_provider=image_acquisition_provider,
        binary_asset_configuration=binary_asset_configuration,
        binary_asset_store=filesystem_binary_asset_store,
        binary_asset_writer=filesystem_binary_asset_store,
        binary_asset_reader=filesystem_binary_asset_store,
        binary_asset_integrity_validator=binary_asset_integrity_validator,
        binary_asset_reconciler=binary_asset_reconciler,
        planning_artifact_reconciler=planning_artifact_reconciler,
        async_resources=(
            image_acquisition_provider,
            visual_asset_planning_provider,
            scene_planning_provider,
            scripting_provider,
            planning_provider,
        ),
    )


def _build_planning_provider(settings: Settings) -> PlanningProvider:
    provider_name = settings.ORION_PLANNING_PROVIDER.strip().lower()
    if provider_name == "simulated":
        return SimulatedPlanningProvider()
    if provider_name != "openrouter":
        raise PlanningProviderConfigurationError(
            f"unsupported planning provider: {provider_name!r}"
        )
    if settings.ORION_PLANNING_API_KEY is None:
        raise PlanningProviderConfigurationError("planning provider credential is missing")
    if not settings.ORION_PLANNING_MODEL.strip():
        raise PlanningProviderConfigurationError("planning model is missing")
    _validate_https_provider_url(
        settings.ORION_PLANNING_BASE_URL,
        error_type=PlanningProviderConfigurationError,
        message="planning base URL is invalid",
    )
    openrouter_provider_factory = load_openrouter_planning_provider()

    return openrouter_provider_factory(
        api_key=settings.ORION_PLANNING_API_KEY.get_secret_value(),
        model=settings.ORION_PLANNING_MODEL,
        prompt_builder=PlanningPromptBuilder(),
        base_url=settings.ORION_PLANNING_BASE_URL,
        timeout_seconds=settings.ORION_PLANNING_TIMEOUT_SECONDS,
        max_transport_attempts=settings.ORION_PLANNING_MAX_TRANSPORT_ATTEMPTS,
        retry_base_delay_seconds=settings.ORION_PLANNING_RETRY_BASE_DELAY_SECONDS,
        max_output_tokens=settings.ORION_PLANNING_MAX_OUTPUT_TOKENS,
        temperature=settings.ORION_PLANNING_TEMPERATURE,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
    )


def _resolve_scripting_provider_factory(
    settings: Settings,
) -> ScriptingProviderFactory | None:
    provider_name = settings.ORION_SCRIPTING_PROVIDER.strip().lower()
    if provider_name == "simulated":
        return None
    if provider_name != "openrouter":
        raise ScriptingProviderConfigurationError(
            f"unsupported scripting provider: {provider_name!r}"
        )
    if settings.ORION_SCRIPTING_API_KEY is None:
        raise ScriptingProviderConfigurationError("scripting provider credential is missing")
    if not settings.ORION_SCRIPTING_MODEL.strip():
        raise ScriptingProviderConfigurationError("scripting model is missing")
    _validate_https_provider_url(
        settings.ORION_SCRIPTING_BASE_URL,
        error_type=ScriptingProviderConfigurationError,
        message="scripting base URL is invalid",
    )
    return load_openrouter_scripting_provider()


def _build_scripting_provider(
    settings: Settings,
    *,
    openrouter_factory: ScriptingProviderFactory | None,
) -> ScriptingProvider:
    if openrouter_factory is None:
        return SimulatedScriptingProvider()
    if settings.ORION_SCRIPTING_API_KEY is None:
        raise ScriptingProviderConfigurationError("scripting provider credential is missing")
    return openrouter_factory(
        api_key=settings.ORION_SCRIPTING_API_KEY.get_secret_value(),
        model=settings.ORION_SCRIPTING_MODEL,
        prompt_builder=ScriptingPromptBuilder(
            max_plan_bytes=settings.ORION_SCRIPTING_MAX_PLAN_BYTES
        ),
        base_url=settings.ORION_SCRIPTING_BASE_URL,
        timeout_seconds=settings.ORION_SCRIPTING_TIMEOUT_SECONDS,
        max_transport_attempts=settings.ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS,
        retry_base_delay_seconds=settings.ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS,
        max_output_tokens=settings.ORION_SCRIPTING_MAX_OUTPUT_TOKENS,
        temperature=settings.ORION_SCRIPTING_TEMPERATURE,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
    )


def _resolve_scene_planning_provider_factory(
    settings: Settings,
) -> ScenePlanningProviderFactory | None:
    provider_name = settings.ORION_SCENE_PLANNING_PROVIDER.strip().lower()
    if provider_name == "simulated":
        return None
    if provider_name != "openrouter":
        raise ScenePlanningProviderConfigurationException(
            f"unsupported scene-planning provider: {provider_name!r}"
        )
    if settings.ORION_SCENE_PLANNING_API_KEY is None:
        raise ScenePlanningProviderConfigurationException(
            "scene-planning provider credential is missing"
        )
    if not settings.ORION_SCENE_PLANNING_MODEL.strip():
        raise ScenePlanningProviderConfigurationException(
            "scene-planning model is missing"
        )
    _validate_https_provider_url(
        settings.ORION_SCENE_PLANNING_BASE_URL,
        error_type=ScenePlanningProviderConfigurationException,
        message="scene-planning base URL is invalid",
    )
    return load_openrouter_scene_planning_provider()


def _build_scene_planning_provider(
    settings: Settings,
    *,
    openrouter_factory: ScenePlanningProviderFactory | None,
) -> ScenePlanningProvider:
    if openrouter_factory is None:
        return SimulatedScenePlanningProvider()
    if settings.ORION_SCENE_PLANNING_API_KEY is None:
        raise ScenePlanningProviderConfigurationException(
            "scene-planning provider credential is missing"
        )
    return openrouter_factory(
        api_key=settings.ORION_SCENE_PLANNING_API_KEY.get_secret_value(),
        model=settings.ORION_SCENE_PLANNING_MODEL,
        prompt_builder=ScenePlanningPromptBuilder(
            max_script_bytes=settings.ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES
        ),
        base_url=settings.ORION_SCENE_PLANNING_BASE_URL,
        timeout_seconds=settings.ORION_SCENE_PLANNING_TIMEOUT_SECONDS,
        max_transport_attempts=(
            settings.ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS
        ),
        retry_base_delay_seconds=(
            settings.ORION_SCENE_PLANNING_RETRY_BASE_DELAY_SECONDS
        ),
        max_output_tokens=settings.ORION_SCENE_PLANNING_MAX_OUTPUT_TOKENS,
        temperature=settings.ORION_SCENE_PLANNING_TEMPERATURE,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
    )


def _resolve_visual_asset_planning_provider_factory(
    settings: Settings,
) -> VisualAssetPlanningProviderFactory | None:
    provider_name = settings.ORION_VISUAL_ASSET_PLANNING_PROVIDER.strip().lower()
    if provider_name == "simulated":
        return None
    if provider_name != "openrouter":
        raise VisualAssetPlanningProviderConfigurationException(
            f"unsupported visual asset planning provider: {provider_name!r}"
        )
    if settings.ORION_VISUAL_ASSET_PLANNING_API_KEY is None:
        raise VisualAssetPlanningProviderConfigurationException(
            "visual asset planning provider credential is missing"
        )
    if not settings.ORION_VISUAL_ASSET_PLANNING_MODEL.strip():
        raise VisualAssetPlanningProviderConfigurationException(
            "visual asset planning model is missing"
        )
    _validate_https_provider_url(
        settings.ORION_VISUAL_ASSET_PLANNING_BASE_URL,
        error_type=VisualAssetPlanningProviderConfigurationException,
        message="visual asset planning base URL is invalid",
    )
    return load_openrouter_visual_asset_planning_provider()


def _build_visual_asset_planning_provider(
    settings: Settings,
    *,
    openrouter_factory: VisualAssetPlanningProviderFactory | None,
) -> VisualAssetPlanningProvider:
    if openrouter_factory is None:
        return SimulatedVisualAssetPlanningProvider()
    if settings.ORION_VISUAL_ASSET_PLANNING_API_KEY is None:
        raise VisualAssetPlanningProviderConfigurationException(
            "visual asset planning provider credential is missing"
        )
    return openrouter_factory(
        api_key=settings.ORION_VISUAL_ASSET_PLANNING_API_KEY.get_secret_value(),
        model=settings.ORION_VISUAL_ASSET_PLANNING_MODEL,
        prompt_builder=VisualAssetPlanningPromptBuilder(
            max_scene_plan_bytes=(
                settings.ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES
            )
        ),
        base_url=settings.ORION_VISUAL_ASSET_PLANNING_BASE_URL,
        timeout_seconds=settings.ORION_VISUAL_ASSET_PLANNING_TIMEOUT_SECONDS,
        max_transport_attempts=(
            settings.ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS
        ),
        retry_base_delay_seconds=(
            settings.ORION_VISUAL_ASSET_PLANNING_RETRY_BASE_DELAY_SECONDS
        ),
        max_output_tokens=settings.ORION_VISUAL_ASSET_PLANNING_MAX_OUTPUT_TOKENS,
        temperature=settings.ORION_VISUAL_ASSET_PLANNING_TEMPERATURE,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
    )


def _resolve_image_acquisition_provider_factory(
    settings: Settings,
) -> ImageAcquisitionProviderFactory | None:
    provider_name = settings.ORION_IMAGE_ACQUISITION_PROVIDER.strip().lower()
    if provider_name == "simulated":
        return None
    if provider_name != "openrouter":
        raise ImageAcquisitionProviderConfigurationException(
            f"unsupported image acquisition provider: {provider_name!r}"
        )
    if settings.ORION_IMAGE_ACQUISITION_API_KEY is None:
        raise ImageAcquisitionProviderConfigurationException(
            "image acquisition provider credential is missing"
        )
    if not settings.ORION_IMAGE_ACQUISITION_MODEL.strip():
        raise ImageAcquisitionProviderConfigurationException(
            "image acquisition model is missing"
        )
    _validate_https_provider_url(
        settings.ORION_IMAGE_ACQUISITION_BASE_URL,
        error_type=ImageAcquisitionProviderConfigurationException,
        message="image acquisition base URL is invalid",
    )
    return load_openrouter_image_acquisition_provider()


def _build_image_acquisition_provider(
    settings: Settings,
    *,
    openrouter_factory: ImageAcquisitionProviderFactory | None,
) -> ImageAcquisitionProvider:
    if openrouter_factory is None:
        return SimulatedImageAcquisitionProvider()
    if settings.ORION_IMAGE_ACQUISITION_API_KEY is None:
        raise ImageAcquisitionProviderConfigurationException(
            "image acquisition provider credential is missing"
        )
    return openrouter_factory(
        api_key=settings.ORION_IMAGE_ACQUISITION_API_KEY.get_secret_value(),
        model=settings.ORION_IMAGE_ACQUISITION_MODEL,
        prompt_builder=ImageGenerationPromptBuilder(
            max_prompt_bytes=settings.ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES
        ),
        base_url=settings.ORION_IMAGE_ACQUISITION_BASE_URL,
        timeout_seconds=settings.ORION_IMAGE_ACQUISITION_TIMEOUT_SECONDS,
        max_transport_attempts=(
            settings.ORION_IMAGE_ACQUISITION_MAX_TRANSPORT_ATTEMPTS
        ),
        retry_base_delay_seconds=(
            settings.ORION_IMAGE_ACQUISITION_RETRY_BASE_DELAY_SECONDS
        ),
        max_response_bytes=settings.ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES,
        max_decoded_image_bytes=(
            settings.ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES
        ),
        provider_only=settings.ORION_IMAGE_ACQUISITION_PROVIDER_ONLY,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
    )


def _validate_https_provider_url(
    value: str,
    *,
    error_type: type[Exception],
    message: str,
) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise error_type(message)
