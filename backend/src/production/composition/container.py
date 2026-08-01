"""Composition root for Production HTTP use cases and simulated runtime."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from backend.src.production.asset_publishing.cleanup import (
    PublishedAssetCleanupService,
)
from backend.src.production.asset_publishing.configuration import (
    AssetPublishingConfiguration,
)
from backend.src.production.asset_publishing.manifest_store import (
    LocalPublishedAssetManifestStore,
)
from backend.src.production.asset_publishing.ports import AssetPublisher
from backend.src.production.asset_publishing.publishers import (
    FilesystemPublisher,
    NullPublisher,
)
from backend.src.production.asset_publishing.reconciliation import (
    PublishedAssetReconciler,
)
from backend.src.production.asset_publishing.service import AssetPublishingService
from backend.src.production.asset_publishing.sources import (
    ManifestPublishableAssetCollector,
)
from backend.src.production.audio_design.asset_store import (
    FilesystemAudioDesignAssetStore,
)
from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.handler import AudioDesignHandler
from backend.src.production.audio_design.manifest_store import (
    LocalAudioDesignManifestStore,
)
from backend.src.production.audio_design.models import AudioAssetKind
from backend.src.production.audio_design.ports import (
    AudioDesignAssetStore,
    MusicGenerationProvider,
    SoundEffectGenerationProvider,
)
from backend.src.production.audio_design.providers import (
    SimulatedMusicGenerationProvider,
    SimulatedSoundEffectGenerationProvider,
)
from backend.src.production.audio_design.reconciliation import (
    AudioDesignReconciler,
)
from backend.src.production.audio_design.source_reader import (
    AudioDesignSourceScriptReaderAdapter,
)
from backend.src.production.audio_design.wav import AudioDesignWavValidator
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
from backend.src.production.infrastructure.persistence.production_image_acquisition_manifest_query_repository import (
    SQLAlchemyImageAcquisitionManifestQueryRepository,
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
from backend.src.production.media_composition.application.handler import (
    MediaCompositionHandler,
)
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.infrastructure import (
    DurableMediaCompositionSourceReader,
    SQLAlchemyMediaCompositionArtifactInventory,
)
from backend.src.production.media_composition.reconciliation import (
    MediaCompositionReconciler,
)
from backend.src.production.media_composition.storage import (
    LocalMediaCompositionStore,
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
from backend.src.production.render_validation.handler import (
    FinalRenderValidationHandler,
)
from backend.src.production.render_validation.probe import FFprobeFinalRenderProbe
from backend.src.production.render_validation.source_reader import (
    VerifiedFinalRenderSourceReader,
)
from backend.src.production.render_validation.store import (
    LocalFinalRenderValidationStore,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.executable_resolver import (
    LocalMediaExecutableResolver,
)
from backend.src.production.rendering.handler import LocalRenderPreparationHandler
from backend.src.production.rendering.manifest_store import (
    LocalRenderPreparationStore,
)
from backend.src.production.rendering.models import RendererKind
from backend.src.production.rendering.ports import LocalRenderer
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner
from backend.src.production.rendering.reconciliation import LocalRenderReconciler
from backend.src.production.rendering.renderers import DryRunRenderer, LocalFFmpegRenderer
from backend.src.production.rendering.source_reader import (
    VerifiedMediaCompositionSourceReader,
)
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
from backend.src.production.runtime.handlers import (
    DurableSubtitleHandler,
    PlanningHandler,
    ScriptingHandler,
)
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
from backend.src.production.scripting.openrouter_billable_gate import (
    OpenRouterScriptingBillablePolicy,
)
from backend.src.production.scripting.openrouter_reconciliation import (
    OpenRouterScriptingRequestReconciler,
)
from backend.src.production.scripting.openrouter_request_store import (
    LocalOpenRouterScriptingRequestStore,
    OpenRouterScriptingRequestStore,
)
from backend.src.production.scripting.ports import ScriptingProvider
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.src.production.scripting.providers.availability import (
    ScriptingProviderFactory,
    load_openrouter_scripting_provider,
)
from backend.src.production.scripting.runtime_readiness import (
    require_scripting_runtime_readiness,
)
from backend.src.production.speech_generation.audio_store import (
    FilesystemSpeechAudioStore,
)
from backend.src.production.speech_generation.capability_sources import (
    StaticSimulatedSpeechCapabilitySource,
)
from backend.src.production.speech_generation.configuration import (
    SpeechGenerationConfiguration,
    SpeechRemotePreparationConfiguration,
)
from backend.src.production.speech_generation.handler import (
    SpeechGenerationHandler,
)
from backend.src.production.speech_generation.manifest_writer import (
    LocalSpeechManifestWriter,
)
from backend.src.production.speech_generation.ports import (
    SpeechAudioStore,
    SpeechGenerationProvider,
)
from backend.src.production.speech_generation.providers import (
    SimulatedSpeechGenerationProvider,
)
from backend.src.production.speech_generation.reconciliation import (
    SpeechGenerationReconciler,
)
from backend.src.production.speech_generation.remote_job_store import (
    LocalRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_ports import (
    SpeechCapabilitySource,
)
from backend.src.production.speech_generation.remote_reconciliation import (
    RemoteSpeechJobReconciler,
)
from backend.src.production.speech_generation.source_reader import (
    SpeechSourceScriptReaderAdapter,
)
from backend.src.production.speech_generation.wav import SpeechWavValidator
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoConfigurationError,
    VideoFramePublicationUnavailableError,
)
from backend.src.production.video_clip_generation.handler import (
    VideoClipGenerationHandler,
)
from backend.src.production.video_clip_generation.manifest_writer import (
    LocalVideoClipManifestWriter,
)
from backend.src.production.video_clip_generation.media_probe import (
    FFprobeMediaProbe,
    VideoClipIntegrityValidator,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipBinaryStore,
    VideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.providers import (
    SimulatedVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.providers.availability import (
    resolve_media_executable,
)
from backend.src.production.video_clip_generation.reader import (
    DurableImageAcquisitionManifestReader,
)
from backend.src.production.video_clip_generation.reconciliation import (
    FilesystemVideoClipReconciler,
)
from backend.src.production.video_clip_generation.video_store import (
    FilesystemVideoClipBinaryStore,
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
    openrouter_scripting_request_store: OpenRouterScriptingRequestStore | None
    openrouter_scripting_reconciler: OpenRouterScriptingRequestReconciler | None
    scene_planning_provider: ScenePlanningProvider
    visual_asset_planning_provider: VisualAssetPlanningProvider
    image_acquisition_provider: ImageAcquisitionProvider
    video_clip_generation_provider: VideoClipGenerationProvider
    speech_generation_provider: SpeechGenerationProvider
    music_generation_provider: MusicGenerationProvider
    sound_effect_generation_provider: SoundEffectGenerationProvider
    speech_capability_source: SpeechCapabilitySource
    speech_remote_configuration: SpeechRemotePreparationConfiguration
    binary_asset_configuration: AssetStorageConfiguration
    binary_asset_store: BinaryAssetStore
    binary_asset_writer: BinaryAssetWriter
    binary_asset_reader: BinaryAssetReader
    binary_asset_integrity_validator: BinaryAssetIntegrityValidator
    binary_asset_reconciler: FilesystemBinaryAssetReconciler
    video_clip_binary_store: VideoClipBinaryStore
    video_clip_integrity_validator: VideoClipIntegrityValidator
    video_clip_reconciler: FilesystemVideoClipReconciler
    speech_audio_store: SpeechAudioStore
    speech_reconciler: SpeechGenerationReconciler
    music_asset_store: AudioDesignAssetStore
    sound_effect_asset_store: AudioDesignAssetStore
    audio_design_reconciler: AudioDesignReconciler
    media_composition_reconciler: MediaCompositionReconciler
    local_renderer: LocalRenderer
    render_reconciler: LocalRenderReconciler
    remote_speech_job_store: LocalRemoteSpeechJobStore
    remote_speech_reconciler: RemoteSpeechJobReconciler
    asset_publisher: AssetPublisher
    asset_publishing_service: AssetPublishingService
    asset_publishing_cleanup: PublishedAssetCleanupService
    asset_publishing_reconciler: PublishedAssetReconciler
    publishable_asset_collector: ManifestPublishableAssetCollector
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
    visual_asset_planning_factory = _resolve_visual_asset_planning_provider_factory(settings)
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
        openrouter_scripting_request_store = (
            LocalOpenRouterScriptingRequestStore(
                settings.PROJECTS_DIR,
                max_bytes=settings.ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES,
            )
            if scripting_factory is not None
            else None
        )
        scripting_provider = _build_scripting_provider(
            settings,
            openrouter_factory=scripting_factory,
            request_store=openrouter_scripting_request_store,
            clock=clock,
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
            max_scene_plan_bytes=(settings.ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES),
        ),
        provider=visual_asset_planning_provider,
        artifact_writer=LocalVisualAssetPlanningArtifactWriter(
            settings.PROJECTS_DIR,
            max_artifact_bytes=(settings.ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES),
        ),
        clock=clock,
        uuid_factory=uuid4,
    )
    binary_asset_configuration = AssetStorageConfiguration(
        workspace=settings.PROJECTS_DIR,
        max_asset_size=settings.ORION_BINARY_ASSET_MAX_SIZE_BYTES,
        allowed_mime_types=frozenset(settings.ORION_BINARY_ASSET_ALLOWED_MIME_TYPES),
        allowed_extensions=frozenset(settings.ORION_BINARY_ASSET_ALLOWED_EXTENSIONS),
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
        requested_model=(settings.ORION_IMAGE_ACQUISITION_MODEL.strip() or None),
        prompt_builder=image_prompt_builder,
        clock=clock,
    )
    video_clip_configuration = VideoClipGenerationConfiguration(
        provider=settings.ORION_VIDEO_CLIP_GENERATION_PROVIDER,
        model=settings.ORION_VIDEO_CLIP_GENERATION_MODEL,
        output_format=settings.ORION_VIDEO_CLIP_GENERATION_OUTPUT_FORMAT,
        codec=settings.ORION_VIDEO_CLIP_GENERATION_CODEC,
        resolution=settings.ORION_VIDEO_CLIP_GENERATION_RESOLUTION,
        generate_audio=settings.ORION_VIDEO_CLIP_GENERATION_GENERATE_AUDIO,
        frame_rate=settings.ORION_VIDEO_CLIP_GENERATION_FRAME_RATE,
        duration_seconds=settings.ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS,
        max_duration_seconds=(settings.ORION_VIDEO_CLIP_GENERATION_MAX_DURATION_SECONDS),
    )
    ffprobe_media_probe = FFprobeMediaProbe(
        executable=resolve_media_executable(
            settings.ORION_VIDEO_CLIP_GENERATION_FFPROBE_PATH,
            "ffprobe",
        )
    )
    video_clip_integrity_validator = VideoClipIntegrityValidator(
        probe=ffprobe_media_probe,
        max_video_bytes=settings.ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES,
    )
    filesystem_video_clip_store = FilesystemVideoClipBinaryStore(
        workspace_root=settings.PROJECTS_DIR,
        integrity_validator=video_clip_integrity_validator,
        max_video_bytes=settings.ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES,
        clock=clock,
    )
    asset_publishing_configuration = AssetPublishingConfiguration(
        publisher=settings.ORION_ASSET_PUBLISHING_PUBLISHER,
        public_root=(
            settings.ORION_ASSET_PUBLISHING_PUBLIC_ROOT
            or settings.PROJECTS_DIR / ".published-assets"
        ),
        public_base_url=settings.ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL,
        lifetime_seconds=settings.ORION_ASSET_PUBLISHING_LIFETIME_SECONDS,
        max_asset_bytes=settings.ORION_ASSET_PUBLISHING_MAX_ASSET_BYTES,
        max_manifest_bytes=settings.ORION_ASSET_PUBLISHING_MAX_MANIFEST_BYTES,
    )
    asset_publisher: AssetPublisher
    if asset_publishing_configuration.publisher == "filesystem":
        asset_publisher = FilesystemPublisher(
            public_root=asset_publishing_configuration.public_root,
            public_base_url=asset_publishing_configuration.public_base_url,
            max_asset_bytes=asset_publishing_configuration.max_asset_bytes,
            clock=clock,
        )
    else:
        asset_publisher = NullPublisher()
    published_asset_manifests = LocalPublishedAssetManifestStore(
        settings.PROJECTS_DIR,
        max_bytes=asset_publishing_configuration.max_manifest_bytes,
    )
    asset_publishing_service = AssetPublishingService(
        publisher=asset_publisher,
        manifest_store=published_asset_manifests,
        lifetime_seconds=asset_publishing_configuration.lifetime_seconds,
        clock=clock,
    )
    asset_publishing_cleanup = PublishedAssetCleanupService(
        publisher=asset_publisher,
        manifests=published_asset_manifests,
        clock=clock,
    )
    asset_publishing_reconciler = PublishedAssetReconciler(
        manifests=published_asset_manifests,
        publisher=asset_publisher,
        clock=clock,
    )
    publishable_asset_collector = ManifestPublishableAssetCollector(
        binary_assets=filesystem_binary_asset_store,
        video_clips=filesystem_video_clip_store,
    )
    video_clip_generation_provider = _build_video_clip_generation_provider(settings)
    image_acquisition_manifest_reader = DurableImageAcquisitionManifestReader(
        workspace_root=settings.PROJECTS_DIR,
        repository=SQLAlchemyImageAcquisitionManifestQueryRepository(sessions),
        binary_reader=filesystem_binary_asset_store,
        max_manifest_bytes=(settings.ORION_VIDEO_CLIP_GENERATION_MAX_SOURCE_MANIFEST_BYTES),
    )
    video_clip_generation_handler = VideoClipGenerationHandler(
        manifest_reader=image_acquisition_manifest_reader,
        provider=video_clip_generation_provider,
        binary_store=filesystem_video_clip_store,
        manifest_writer=LocalVideoClipManifestWriter(
            settings.PROJECTS_DIR,
            max_manifest_bytes=(settings.ORION_VIDEO_CLIP_GENERATION_MAX_MANIFEST_BYTES),
        ),
        configuration=video_clip_configuration,
        clock=clock,
    )
    speech_configuration = SpeechGenerationConfiguration(
        provider=settings.ORION_SPEECH_GENERATION_PROVIDER,
        voice=settings.ORION_SPEECH_GENERATION_VOICE,
        language=settings.ORION_SPEECH_GENERATION_LANGUAGE,
        words_per_minute=settings.ORION_SPEECH_GENERATION_WORDS_PER_MINUTE,
        sample_rate_hz=settings.ORION_SPEECH_GENERATION_SAMPLE_RATE_HZ,
        channel_count=settings.ORION_SPEECH_GENERATION_CHANNEL_COUNT,
        sample_width_bytes=settings.ORION_SPEECH_GENERATION_SAMPLE_WIDTH_BYTES,
        min_duration_ms=settings.ORION_SPEECH_GENERATION_MIN_DURATION_MS,
        max_segment_duration_ms=(settings.ORION_SPEECH_GENERATION_MAX_SEGMENT_DURATION_MS),
        max_audio_bytes=settings.ORION_SPEECH_GENERATION_MAX_AUDIO_BYTES,
        max_manifest_bytes=settings.ORION_SPEECH_GENERATION_MAX_MANIFEST_BYTES,
        max_script_bytes=settings.ORION_SPEECH_GENERATION_MAX_SCRIPT_BYTES,
        generating_stale_after_seconds=(
            settings.ORION_SPEECH_GENERATION_GENERATING_STALE_AFTER_SECONDS
        ),
    )
    speech_generation_provider = SimulatedSpeechGenerationProvider()
    speech_capability_source = StaticSimulatedSpeechCapabilitySource(
        configuration=speech_configuration,
        clock=clock,
    )
    speech_remote_configuration = SpeechRemotePreparationConfiguration(
        allow_billable_requests=(settings.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS),
        remote_provider=settings.ORION_SPEECH_GENERATION_REMOTE_PROVIDER,
        remote_model=settings.ORION_SPEECH_GENERATION_REMOTE_MODEL,
        remote_voice=settings.ORION_SPEECH_GENERATION_REMOTE_VOICE,
        maximum_estimated_cost=(settings.ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST),
        max_poll_attempts=(settings.ORION_SPEECH_GENERATION_REMOTE_MAX_POLL_ATTEMPTS),
        poll_interval_seconds=(settings.ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS),
        remote_job_max_bytes=(settings.ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES),
    )
    speech_source_reader = SpeechSourceScriptReaderAdapter(
        DurableProductionScriptReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScriptQueryRepository(sessions),
            max_script_bytes=speech_configuration.max_script_bytes,
        )
    )
    speech_audio_store = FilesystemSpeechAudioStore(
        workspace_root=settings.PROJECTS_DIR,
        validator=SpeechWavValidator(max_audio_bytes=speech_configuration.max_audio_bytes),
        max_audio_bytes=speech_configuration.max_audio_bytes,
        clock=clock,
    )
    speech_generation_handler = SpeechGenerationHandler(
        script_reader=speech_source_reader,
        provider=speech_generation_provider,
        audio_store=speech_audio_store,
        manifest_writer=LocalSpeechManifestWriter(
            settings.PROJECTS_DIR,
            max_manifest_bytes=speech_configuration.max_manifest_bytes,
        ),
        configuration=speech_configuration,
        clock=clock,
    )
    audio_design_configuration = AudioDesignConfiguration(
        music_provider=settings.ORION_MUSIC_GENERATION_PROVIDER,
        sound_effect_provider=settings.ORION_SOUND_EFFECT_GENERATION_PROVIDER,
        sample_rate_hz=settings.ORION_AUDIO_DESIGN_SAMPLE_RATE_HZ,
        channel_count=settings.ORION_AUDIO_DESIGN_CHANNEL_COUNT,
        sample_width_bytes=settings.ORION_AUDIO_DESIGN_SAMPLE_WIDTH_BYTES,
        min_music_duration_ms=settings.ORION_AUDIO_DESIGN_MIN_MUSIC_DURATION_MS,
        max_music_duration_ms=settings.ORION_AUDIO_DESIGN_MAX_MUSIC_DURATION_MS,
        min_sound_effect_duration_ms=(settings.ORION_AUDIO_DESIGN_MIN_SOUND_EFFECT_DURATION_MS),
        max_sound_effect_duration_ms=(settings.ORION_AUDIO_DESIGN_MAX_SOUND_EFFECT_DURATION_MS),
        max_audio_bytes=settings.ORION_AUDIO_DESIGN_MAX_AUDIO_BYTES,
        max_manifest_bytes=settings.ORION_AUDIO_DESIGN_MAX_MANIFEST_BYTES,
        max_script_bytes=settings.ORION_AUDIO_DESIGN_MAX_SCRIPT_BYTES,
        generating_stale_after_seconds=(settings.ORION_AUDIO_DESIGN_GENERATING_STALE_AFTER_SECONDS),
    )
    music_generation_provider = SimulatedMusicGenerationProvider(audio_design_configuration)
    sound_effect_generation_provider = SimulatedSoundEffectGenerationProvider(
        audio_design_configuration
    )
    audio_design_source_reader = AudioDesignSourceScriptReaderAdapter(
        DurableProductionScriptReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScriptQueryRepository(sessions),
            max_script_bytes=audio_design_configuration.max_script_bytes,
        )
    )
    audio_design_wav_validator = AudioDesignWavValidator(
        max_audio_bytes=audio_design_configuration.max_audio_bytes
    )
    music_asset_store = FilesystemAudioDesignAssetStore(
        workspace_root=settings.PROJECTS_DIR,
        kind=AudioAssetKind.MUSIC,
        validator=audio_design_wav_validator,
        max_audio_bytes=audio_design_configuration.max_audio_bytes,
    )
    sound_effect_asset_store = FilesystemAudioDesignAssetStore(
        workspace_root=settings.PROJECTS_DIR,
        kind=AudioAssetKind.SOUND_EFFECT,
        validator=audio_design_wav_validator,
        max_audio_bytes=audio_design_configuration.max_audio_bytes,
    )
    audio_design_handler = AudioDesignHandler(
        script_reader=audio_design_source_reader,
        music_provider=music_generation_provider,
        sound_effect_provider=sound_effect_generation_provider,
        music_store=music_asset_store,
        sound_effect_store=sound_effect_asset_store,
        manifest_store=LocalAudioDesignManifestStore(
            settings.PROJECTS_DIR,
            max_manifest_bytes=audio_design_configuration.max_manifest_bytes,
        ),
        configuration=audio_design_configuration,
        clock=clock,
    )
    audio_design_reconciler = AudioDesignReconciler(
        workspace_root=settings.PROJECTS_DIR,
        script_reader=audio_design_source_reader,
        music_store=music_asset_store,
        sound_effect_store=sound_effect_asset_store,
        configuration=audio_design_configuration,
    )
    media_composition_configuration = MediaCompositionConfiguration(
        max_source_manifest_bytes=(settings.ORION_MEDIA_COMPOSITION_MAX_SOURCE_MANIFEST_BYTES),
        max_plan_bytes=settings.ORION_MEDIA_COMPOSITION_MAX_PLAN_BYTES,
        max_manifest_bytes=settings.ORION_MEDIA_COMPOSITION_MAX_MANIFEST_BYTES,
    )
    media_composition_store = LocalMediaCompositionStore(
        settings.PROJECTS_DIR,
        max_plan_bytes=media_composition_configuration.max_plan_bytes,
        max_manifest_bytes=media_composition_configuration.max_manifest_bytes,
    )
    media_composition_source_reader = DurableMediaCompositionSourceReader(
        workspace_root=settings.PROJECTS_DIR,
        inventory=SQLAlchemyMediaCompositionArtifactInventory(artifacts),
        script_reader=DurableProductionScriptReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScriptQueryRepository(sessions),
            max_script_bytes=audio_design_configuration.max_script_bytes,
        ),
        scene_plan_reader=DurableProductionScenePlanReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScenePlanQueryRepository(sessions),
            max_scene_plan_bytes=(media_composition_configuration.max_source_manifest_bytes),
        ),
        audio_design_configuration=audio_design_configuration,
        configuration=media_composition_configuration,
    )
    media_composition_handler = MediaCompositionHandler(
        source_reader=media_composition_source_reader,
        store=media_composition_store,
        configuration=media_composition_configuration,
        clock=clock,
    )
    media_composition_reconciler = MediaCompositionReconciler(
        source_reader=media_composition_source_reader,
        store=media_composition_store,
        configuration=media_composition_configuration,
    )
    subtitle_handler = DurableSubtitleHandler(
        script_reader=DurableProductionScriptReader(
            workspace_root=settings.PROJECTS_DIR,
            repository=SQLAlchemyProductionScriptQueryRepository(sessions),
            max_script_bytes=settings.ORION_SCRIPTING_MAX_SCRIPT_BYTES,
        ),
        workspace_root=settings.PROJECTS_DIR,
        clock=clock,
    )
    rendering_configuration = RenderingConfiguration(
        renderer=RendererKind(settings.ORION_RENDERER),
        ffmpeg_path=(Path(settings.ORION_FFMPEG_PATH) if settings.ORION_FFMPEG_PATH else None),
        ffprobe_path=(Path(settings.ORION_FFPROBE_PATH) if settings.ORION_FFPROBE_PATH else None),
        output_container=settings.ORION_RENDER_OUTPUT_CONTAINER,
        video_codec=settings.ORION_RENDER_VIDEO_CODEC,
        audio_codec=settings.ORION_RENDER_AUDIO_CODEC,
        pixel_format=settings.ORION_RENDER_PIXEL_FORMAT,
        video_preset=settings.ORION_RENDER_VIDEO_PRESET,
        video_crf=settings.ORION_RENDER_VIDEO_CRF,
        audio_bitrate=settings.ORION_RENDER_AUDIO_BITRATE,
        process_timeout_seconds=settings.ORION_RENDER_PROCESS_TIMEOUT_SECONDS,
        probe_timeout_seconds=settings.ORION_RENDER_PROBE_TIMEOUT_SECONDS,
        max_stderr_bytes=settings.ORION_RENDER_MAX_STDERR_BYTES,
        max_output_bytes=settings.ORION_RENDER_MAX_OUTPUT_BYTES,
        duration_tolerance_ms=settings.ORION_RENDER_DURATION_TOLERANCE_MS,
        frame_rate_tolerance=settings.ORION_RENDER_FRAME_RATE_TOLERANCE,
        max_request_bytes=settings.ORION_RENDER_MAX_REQUEST_BYTES,
        max_manifest_bytes=settings.ORION_RENDER_MAX_MANIFEST_BYTES,
        max_execution_plan_bytes=settings.ORION_RENDER_MAX_EXECUTION_PLAN_BYTES,
    )
    render_store = LocalRenderPreparationStore(
        settings.PROJECTS_DIR,
        max_request_bytes=rendering_configuration.max_request_bytes,
        max_manifest_bytes=rendering_configuration.max_manifest_bytes,
        max_execution_plan_bytes=rendering_configuration.max_execution_plan_bytes,
    )
    render_source_reader = VerifiedMediaCompositionSourceReader(
        workspace_root=settings.PROJECTS_DIR,
        inventory=SQLAlchemyMediaCompositionArtifactInventory(artifacts),
        max_plan_bytes=media_composition_configuration.max_plan_bytes,
        max_manifest_bytes=media_composition_configuration.max_manifest_bytes,
    )
    local_renderer: LocalRenderer
    final_render_validation_handler: FinalRenderValidationHandler | None = None
    if rendering_configuration.renderer is RendererKind.DRY_RUN:
        local_renderer = DryRunRenderer()
    else:
        resolved_render_binaries = LocalMediaExecutableResolver().resolve(
            ffmpeg_path=rendering_configuration.ffmpeg_path,
            ffprobe_path=rendering_configuration.ffprobe_path,
        )
        render_process_runner = ControlledMediaProcessRunner(
            ffmpeg_path=resolved_render_binaries.ffmpeg,
            ffprobe_path=resolved_render_binaries.ffprobe,
            stderr_limit=rendering_configuration.max_stderr_bytes,
        )
        local_renderer = LocalFFmpegRenderer(
            workspace_root=settings.PROJECTS_DIR,
            runner=render_process_runner,
        )
        final_render_validation_handler = FinalRenderValidationHandler(
            source_reader=VerifiedFinalRenderSourceReader(
                workspace_root=settings.PROJECTS_DIR,
                inventory=SQLAlchemyMediaCompositionArtifactInventory(artifacts),
                max_json_bytes=max(
                    rendering_configuration.max_request_bytes,
                    rendering_configuration.max_manifest_bytes,
                    rendering_configuration.max_execution_plan_bytes,
                    media_composition_configuration.max_plan_bytes,
                    media_composition_configuration.max_manifest_bytes,
                ),
            ),
            store=LocalFinalRenderValidationStore(
                workspace_root=settings.PROJECTS_DIR,
                max_manifest_bytes=(settings.ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES),
            ),
            probe=FFprobeFinalRenderProbe(runner=render_process_runner),
            clock=clock,
        )
    render_handler = LocalRenderPreparationHandler(
        source_reader=render_source_reader,
        store=render_store,
        renderer=local_renderer,
        configuration=rendering_configuration,
        clock=clock,
    )
    render_reconciler = LocalRenderReconciler(
        source_reader=render_source_reader,
        store=render_store,
        configuration=rendering_configuration,
        renderer=local_renderer,
        artifact_inventory=SQLAlchemyMediaCompositionArtifactInventory(artifacts),
    )
    binary_asset_reconciler = FilesystemBinaryAssetReconciler(
        configuration=binary_asset_configuration,
        store=filesystem_binary_asset_store,
    )
    video_clip_reconciler = FilesystemVideoClipReconciler(
        workspace_root=settings.PROJECTS_DIR,
        store=filesystem_video_clip_store,
        source_reader=image_acquisition_manifest_reader,
        registered_reader=SQLAlchemyRegisteredPlanningArtifactReader(
            sessions,
            artifact_types=frozenset(
                {
                    ArtifactType.SOURCE_VIDEO_CLIP,
                    ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
                }
            ),
        ),
        max_manifest_bytes=settings.ORION_VIDEO_CLIP_GENERATION_MAX_MANIFEST_BYTES,
    )
    speech_reconciler = SpeechGenerationReconciler(
        workspace_root=settings.PROJECTS_DIR,
        audio_store=speech_audio_store,
        source_reader=speech_source_reader,
        registered_reader=SQLAlchemyRegisteredPlanningArtifactReader(
            sessions,
            artifact_types=frozenset(
                {
                    ArtifactType.NARRATION,
                    ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
                    ArtifactType.MEDIA_COMPOSITION_PLAN,
                    ArtifactType.MEDIA_COMPOSITION_MANIFEST,
                }
            ),
        ),
        max_manifest_bytes=speech_configuration.max_manifest_bytes,
    )
    remote_speech_job_store = LocalRemoteSpeechJobStore(
        settings.PROJECTS_DIR,
        max_bytes=speech_remote_configuration.remote_job_max_bytes,
    )
    remote_speech_reconciler = RemoteSpeechJobReconciler(
        workspace_root=settings.PROJECTS_DIR,
        audio_store=speech_audio_store,
        max_record_bytes=speech_remote_configuration.remote_job_max_bytes,
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
                    ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
                    ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
                }
            ),
        ),
        minimum_age_seconds=settings.ORION_PLANNING_ORPHAN_MIN_AGE_SECONDS,
        action=settings.ORION_PLANNING_ORPHAN_ACTION,
        quarantine_relative_path=settings.ORION_PLANNING_QUARANTINE_DIR,
        clock=clock,
        binary_reconciler=binary_asset_reconciler,
        video_reconciler=video_clip_reconciler,
    )

    leases = ProductionLeaseManager(
        SQLAlchemyLeaseRepository(sessions),
        clock=clock,
        lease_duration=timedelta(seconds=settings.ORION_PRODUCTION_LEASE_DURATION_SECONDS),
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
            interval=timedelta(seconds=settings.ORION_PRODUCTION_HEARTBEAT_INTERVAL_SECONDS),
        ),
        executor=ProductionExecutor(
            create_handler_registry(
                planning_handler=planning_handler,
                scripting_handler=scripting_handler,
                scene_planning_handler=scene_planning_handler,
                visual_asset_planning_handler=visual_asset_planning_handler,
                image_acquisition_handler=image_acquisition_handler,
                video_clip_generation_handler=video_clip_generation_handler,
                speech_generation_handler=speech_generation_handler,
                audio_design_handler=audio_design_handler,
                subtitle_handler=subtitle_handler,
                media_composition_handler=media_composition_handler,
                render_handler=render_handler,
                final_render_validation_handler=final_render_validation_handler,
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
        openrouter_scripting_request_store=openrouter_scripting_request_store,
        openrouter_scripting_reconciler=(
            OpenRouterScriptingRequestReconciler(openrouter_scripting_request_store)
            if openrouter_scripting_request_store is not None
            else None
        ),
        scene_planning_provider=scene_planning_provider,
        visual_asset_planning_provider=visual_asset_planning_provider,
        image_acquisition_provider=image_acquisition_provider,
        video_clip_generation_provider=video_clip_generation_provider,
        speech_generation_provider=speech_generation_provider,
        music_generation_provider=music_generation_provider,
        sound_effect_generation_provider=sound_effect_generation_provider,
        speech_capability_source=speech_capability_source,
        speech_remote_configuration=speech_remote_configuration,
        binary_asset_configuration=binary_asset_configuration,
        binary_asset_store=filesystem_binary_asset_store,
        binary_asset_writer=filesystem_binary_asset_store,
        binary_asset_reader=filesystem_binary_asset_store,
        binary_asset_integrity_validator=binary_asset_integrity_validator,
        binary_asset_reconciler=binary_asset_reconciler,
        video_clip_binary_store=filesystem_video_clip_store,
        video_clip_integrity_validator=video_clip_integrity_validator,
        video_clip_reconciler=video_clip_reconciler,
        speech_audio_store=speech_audio_store,
        speech_reconciler=speech_reconciler,
        music_asset_store=music_asset_store,
        sound_effect_asset_store=sound_effect_asset_store,
        audio_design_reconciler=audio_design_reconciler,
        media_composition_reconciler=media_composition_reconciler,
        local_renderer=local_renderer,
        render_reconciler=render_reconciler,
        remote_speech_job_store=remote_speech_job_store,
        remote_speech_reconciler=remote_speech_reconciler,
        asset_publisher=asset_publisher,
        asset_publishing_service=asset_publishing_service,
        asset_publishing_cleanup=asset_publishing_cleanup,
        asset_publishing_reconciler=asset_publishing_reconciler,
        publishable_asset_collector=publishable_asset_collector,
        planning_artifact_reconciler=planning_artifact_reconciler,
        async_resources=(
            video_clip_generation_provider,
            image_acquisition_provider,
            visual_asset_planning_provider,
            scene_planning_provider,
            scripting_provider,
            planning_provider,
            speech_generation_provider,
            music_generation_provider,
            sound_effect_generation_provider,
            speech_capability_source,
            local_renderer,
            asset_publisher,
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
    readiness = require_scripting_runtime_readiness(
        provider=settings.ORION_SCRIPTING_PROVIDER,
        api_key_configured=settings.ORION_SCRIPTING_API_KEY is not None,
        model=settings.ORION_SCRIPTING_MODEL,
    )
    if readiness.configured_provider == "simulated":
        return None
    if not settings.ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS:
        raise ScriptingProviderConfigurationError(
            "OpenRouter scripting requires explicit billable authorization"
        )
    if settings.ORION_SCRIPTING_ESTIMATED_COST_USD is None:
        raise ScriptingProviderConfigurationError("OpenRouter scripting cost estimate is missing")
    if settings.ORION_SCRIPTING_MAX_ESTIMATED_COST_USD is None:
        raise ScriptingProviderConfigurationError(
            "OpenRouter scripting cost authorization is missing"
        )
    _validate_https_provider_url(
        settings.ORION_SCRIPTING_BASE_URL,
        error_type=ScriptingProviderConfigurationError,
        message="scripting base URL is invalid",
    )
    parsed_url = urlsplit(settings.ORION_SCRIPTING_BASE_URL)
    if (
        (parsed_url.hostname or "").lower() != "openrouter.ai"
        or parsed_url.port is not None
        or parsed_url.path.rstrip("/") != "/api/v1"
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ScriptingProviderConfigurationError(
            "scripting base URL must be the controlled OpenRouter endpoint"
        )
    return load_openrouter_scripting_provider()


def _build_scripting_provider(
    settings: Settings,
    *,
    openrouter_factory: ScriptingProviderFactory | None,
    request_store: OpenRouterScriptingRequestStore | None,
    clock: Callable[[], datetime],
) -> ScriptingProvider:
    if openrouter_factory is None:
        return SimulatedScriptingProvider()
    if settings.ORION_SCRIPTING_API_KEY is None:
        raise ScriptingProviderConfigurationError("scripting provider credential is missing")
    if request_store is None:
        raise ScriptingProviderConfigurationError("OpenRouter scripting request store is missing")
    return openrouter_factory(
        api_key=settings.ORION_SCRIPTING_API_KEY.get_secret_value(),
        model=settings.ORION_SCRIPTING_MODEL,
        prompt_builder=ScriptingPromptBuilder(
            max_plan_bytes=settings.ORION_SCRIPTING_MAX_PLAN_BYTES
        ),
        request_store=request_store,
        billable_policy=OpenRouterScriptingBillablePolicy(
            allow_billable_requests=settings.ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS,
            estimated_cost_usd=settings.ORION_SCRIPTING_ESTIMATED_COST_USD,
            maximum_authorized_cost_usd=(settings.ORION_SCRIPTING_MAX_ESTIMATED_COST_USD),
        ),
        base_url=settings.ORION_SCRIPTING_BASE_URL,
        timeout_seconds=settings.ORION_SCRIPTING_TIMEOUT_SECONDS,
        max_transport_attempts=settings.ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS,
        retry_base_delay_seconds=settings.ORION_SCRIPTING_RETRY_BASE_DELAY_SECONDS,
        max_output_tokens=settings.ORION_SCRIPTING_MAX_OUTPUT_TOKENS,
        temperature=settings.ORION_SCRIPTING_TEMPERATURE,
        max_response_bytes=settings.ORION_SCRIPTING_MAX_RESPONSE_BYTES,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
        clock=clock,
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
        raise ScenePlanningProviderConfigurationException("scene-planning model is missing")
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
        max_transport_attempts=(settings.ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS),
        retry_base_delay_seconds=(settings.ORION_SCENE_PLANNING_RETRY_BASE_DELAY_SECONDS),
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
            max_scene_plan_bytes=(settings.ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES)
        ),
        base_url=settings.ORION_VISUAL_ASSET_PLANNING_BASE_URL,
        timeout_seconds=settings.ORION_VISUAL_ASSET_PLANNING_TIMEOUT_SECONDS,
        max_transport_attempts=(settings.ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS),
        retry_base_delay_seconds=(settings.ORION_VISUAL_ASSET_PLANNING_RETRY_BASE_DELAY_SECONDS),
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
        raise ImageAcquisitionProviderConfigurationException("image acquisition model is missing")
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
        max_transport_attempts=(settings.ORION_IMAGE_ACQUISITION_MAX_TRANSPORT_ATTEMPTS),
        retry_base_delay_seconds=(settings.ORION_IMAGE_ACQUISITION_RETRY_BASE_DELAY_SECONDS),
        max_response_bytes=settings.ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES,
        max_decoded_image_bytes=(settings.ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES),
        provider_only=settings.ORION_IMAGE_ACQUISITION_PROVIDER_ONLY,
        http_referer=settings.ORION_OPENROUTER_HTTP_REFERER,
        app_title=settings.ORION_OPENROUTER_APP_TITLE,
    )


def _build_video_clip_generation_provider(
    settings: Settings,
) -> VideoClipGenerationProvider:
    if settings.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "simulated":
        return SimulatedVideoClipGenerationProvider(
            ffmpeg_path=resolve_media_executable(
                settings.ORION_VIDEO_CLIP_GENERATION_FFMPEG_PATH,
                "ffmpeg",
            ),
            max_output_bytes=settings.ORION_VIDEO_CLIP_GENERATION_MAX_VIDEO_BYTES,
        )
    if not settings.ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS:
        raise OpenRouterVideoConfigurationError(
            "OpenRouter video requires explicit billable authorization"
        )
    if settings.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_API_KEY is None:
        raise OpenRouterVideoConfigurationError("OpenRouter video credential is missing")
    if settings.ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER == "disabled":
        raise VideoFramePublicationUnavailableError(
            "OpenRouter video requires a real secure frame publisher"
        )
    raise OpenRouterVideoConfigurationError(
        "configured OpenRouter video frame publisher is unsupported"
    )


def _validate_https_provider_url(
    value: str,
    *,
    error_type: type[Exception],
    message: str,
) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "openrouter.ai"
        or parsed.path.rstrip("/") != "/api/v1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise error_type(message)
