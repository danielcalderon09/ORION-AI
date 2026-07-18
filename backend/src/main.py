"""FastAPI application entry point."""
# ruff: noqa: F401, I001

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.src.api.v1.router import api_router
from backend.src.api.websocket.progress_socket import setup_websocket
from backend.src.infrastructure.di.container import Container
from backend.src.infrastructure.config.settings import Settings, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    container = Container()
    container.init_resources()
    app.container = container

    # Initialize database
    db = container.database()
    db.create_tables()

    # Register providers in capability registry
    registry = container.capability_registry()
    from backend.src.agents.vision_agent.application.extract_visual_features import OpenCVSceneDetectionProvider
    from backend.src.agents.audio_agent.application.extract_audio_features import LibrosaAudioProvider
    from backend.src.agents.speech_agent.application.transcribe_speech import FasterWhisperProvider
    from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import HeuristicNarrativeProvider
    from backend.src.agents.attention_agent.application.estimate_attention import HeuristicAttentionProvider
    from backend.src.agents.qa_agent.application.qa_service import BasicQAProvider

    from backend.src.infrastructure.model_registry.capability_registry import ModelMetadata

    registry.register(ModelMetadata(
        model_id="opencv_scenes", capability="scene_detection",
        provider_class=OpenCVSceneDetectionProvider, version="0.1.0",
        description="OpenCV histogram scene detection", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="librosa_audio", capability="audio_analysis",
        provider_class=LibrosaAudioProvider, version="0.1.0",
        description="Librosa audio feature extraction", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="faster_whisper_base", capability="speech_recognition",
        provider_class=FasterWhisperProvider, version="0.1.0",
        description="Faster Whisper Base model", requirements=["faster-whisper"], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="heuristic_narrative", capability="narrative_analysis",
        provider_class=HeuristicNarrativeProvider, version="0.1.0",
        description="Heuristic narrative structure", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="heuristic_attention", capability="attention_estimation",
        provider_class=HeuristicAttentionProvider, version="0.1.0",
        description="Heuristic attention estimation", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="basic_qa", capability="quality_validation",
        provider_class=BasicQAProvider, version="0.1.0",
        description="Basic QA validation via ffprobe", requirements=[], default=True,
    ))

    # Sprint 2 providers
    from backend.src.cognition.video_understanding.infrastructure.clip_provider import CLIPUnderstandingProvider
    from backend.src.cognition.video_understanding.i_video_understanding_provider import DummyVideoUnderstandingProvider

    registry.register(ModelMetadata(
        model_id="clip_vit_b32", capability="video_understanding",
        provider_class=CLIPUnderstandingProvider, version="0.2.0",
        description="CLIP ViT-B/32 for zero-shot video understanding", requirements=["transformers", "torch"], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="dummy_vu", capability="video_understanding",
        provider_class=DummyVideoUnderstandingProvider, version="0.1.0",
        description="Dummy video understanding (heuristic fallback)", requirements=[], default=False,
    ))

    # Sprint 3 providers
    from backend.src.viral_intelligence.viral_score_engine.application.viral_score_agent import (
        HookFactorProvider, EmotionFactorProvider, CuriosityFactorProvider,
        VisualPacingFactorProvider, SpeechPacingFactorProvider,
        NoveltyFactorProvider, RetentionPredictionFactorProvider,
    )
    from backend.src.viral_intelligence.hook_optimizer.application.hook_optimizer_agent import (
        PeakHookStrategy, SilenceTrimStrategy, ReactionHookStrategy,
    )

    registry.register(ModelMetadata(
        model_id="hook_factor", capability="viral_factor",
        provider_class=HookFactorProvider, version="0.3.0",
        description="Hook strength viral factor", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="emotion_factor", capability="viral_factor",
        provider_class=EmotionFactorProvider, version="0.3.0",
        description="Emotional impact viral factor", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="peak_hook", capability="hook_strategy",
        provider_class=PeakHookStrategy, version="0.3.0",
        description="Jump-to-peak hook optimization", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="silence_trim", capability="hook_strategy",
        provider_class=SilenceTrimStrategy, version="0.3.0",
        description="Silence-trimming hook optimization", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="reaction_hook", capability="hook_strategy",
        provider_class=ReactionHookStrategy, version="0.3.0",
        description="Reaction-based hook optimization", requirements=[], default=True,
    ))

    # Sprint 4 providers
    from backend.src.sprint4.reflection_engine.application.reflection_engine_agent import ReflectionEngineAgent
    from backend.src.sprint4.critic_ai.application.critic_ai_agent import CriticAIAgent
    from backend.src.sprint4.multi_candidate_generator.application.multi_candidate_generator_agent import MultiCandidateGeneratorAgent
    from backend.src.sprint4.consensus_engine.application.consensus_engine_agent import ConsensusEngineAgent
    from backend.src.sprint4.creative_memory.infrastructure.file_system_creative_memory import FileSystemCreativeMemory
    from backend.src.sprint4.human_feedback.infrastructure.file_system_feedback import FileSystemFeedbackCollector

    registry.register(ModelMetadata(
        model_id="reflection_engine", capability="reflection",
        provider_class=ReflectionEngineAgent, version="0.4.0",
        description="Analyzes clips and proposes improvements", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="critic_ai", capability="critique",
        provider_class=CriticAIAgent, version="0.4.0",
        description="Independent multi-axis quality critic", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="multi_candidate_generator", capability="candidate_generation",
        provider_class=MultiCandidateGeneratorAgent, version="0.4.0",
        description="Generates multiple clip variants", requirements=[], default=True,
    ))
    registry.register(ModelMetadata(
        model_id="consensus_engine", capability="consensus",
        provider_class=ConsensusEngineAgent, version="0.4.0",
        description="Weighted deliberation among expert agents", requirements=[], default=True,
    ))

    # Setup websocket inside async lifespan so it can be awaited
    await setup_websocket(app)

    yield

    # Shutdown
    container.shutdown_resources()


def create_app(app_settings: Settings | None = None) -> FastAPI:
    selected_settings = app_settings or settings

    @asynccontextmanager
    async def app_lifespan(app: FastAPI):
        async with lifespan(app):
            if selected_settings.ORION_PROMPT_VIDEO_ENABLED:
                from backend.src.production.composition.lifecycle import (
                    production_lifespan,
                )

                async with production_lifespan(app, selected_settings):
                    yield
            else:
                yield

    app = FastAPI(
        title=selected_settings.APP_NAME,
        version=selected_settings.APP_VERSION,
        lifespan=app_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict to Electron origin
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api/v1")
    if selected_settings.ORION_PROMPT_VIDEO_ENABLED:
        from backend.src.production.api.router import router as production_router

        app.include_router(production_router, prefix="/api/v1")
    # setup_websocket is now called inside the async lifespan to allow awaiting

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.src.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
    )
