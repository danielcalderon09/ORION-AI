"""Integration test for Sprint 5 production hardening."""
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from backend.src.core.domain.entities.video_project import VideoProject
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
from backend.src.infrastructure.messaging.event_bus import EventBus
from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter

from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
from backend.src.agents.speech_agent.application.transcribe_speech import SpeechAgent
from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import NarrativeIntelligenceAgent
from backend.src.agents.dop_agent.application.dop_service import DoPAgent
from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
from backend.src.agents.qa_agent.application.qa_service import QAAgent

from backend.src.cognition.video_understanding.application.video_understanding_agent import VideoUnderstandingAgent
from backend.src.cognition.video_understanding.i_video_understanding_provider import DummyVideoUnderstandingProvider

from backend.src.viral_intelligence.viral_score_engine.application.viral_score_agent import ViralScoreEngineAgent
from backend.src.viral_intelligence.hook_optimizer.application.hook_optimizer_agent import HookOptimizerAgent
from backend.src.viral_intelligence.retention_simulator.application.retention_simulator_agent import RetentionSimulatorAgent
from backend.src.viral_intelligence.audience_director.application.audience_director_agent import AudienceDirectorAgent
from backend.src.viral_intelligence.creative_director_ai.application.creative_director_agent import CreativeDirectorAgent

from backend.src.sprint4.reflection_engine.application.reflection_engine_agent import ReflectionEngineAgent
from backend.src.sprint4.critic_ai.application.critic_ai_agent import CriticAIAgent
from backend.src.sprint4.multi_candidate_generator.application.multi_candidate_generator_agent import MultiCandidateGeneratorAgent
from backend.src.sprint4.consensus_engine.application.consensus_engine_agent import ConsensusEngineAgent
from backend.src.sprint4.creative_memory.infrastructure.file_system_creative_memory import FileSystemCreativeMemory
from backend.src.sprint4.human_feedback.infrastructure.file_system_feedback import FileSystemFeedbackCollector, SimpleFeedbackLearner

from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator

from backend.src.infrastructure.profiler.performance_profiler import PerformanceProfiler
from backend.src.infrastructure.memory_manager.memory_manager import MemoryManager
from backend.src.infrastructure.checkpoint.checkpoint_manager import CheckpointManager
from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache
from backend.src.infrastructure.observability.observability_stack import ObservabilityStack
from backend.src.infrastructure.versioning.versioning_manager import VersioningManager


def test_sprint5_components_exist():
    assert PerformanceProfiler is not None
    assert MemoryManager is not None
    assert CheckpointManager is not None
    assert PipelineCache is not None
    assert ObservabilityStack is not None
    assert VersioningManager is not None


def test_sprint5_orchestrator_init():
    media = FFmpegMediaAdapter()
    fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
    vu_agent = VideoUnderstandingAgent(DummyVideoUnderstandingProvider())
    feedback_collector = FileSystemFeedbackCollector()

    orchestrator = Sprint5Orchestrator(
        event_bus=EventBus(),
        telemetry=TelemetryService(),
        benchmark=BenchmarkSuite(),
        vision_agent=VisionAgent(media),
        audio_agent=AudioAgent(media),
        speech_agent=SpeechAgent(),
        attention_agent=AttentionAgent(),
        narrative_agent=NarrativeIntelligenceAgent(),
        video_understanding_agent=vu_agent,
        viral_score_agent=ViralScoreEngineAgent(),
        hook_optimizer=HookOptimizerAgent(),
        retention_simulator=RetentionSimulatorAgent(),
        audience_director=AudienceDirectorAgent(),
        creative_director=CreativeDirectorAgent(),
        dop_agent=DoPAgent(),
        exporter_agent=ExporterAgent(media),
        qa_agent=QAAgent(),
        reflection_engine=ReflectionEngineAgent(),
        critic_ai=CriticAIAgent(),
        candidate_generator=MultiCandidateGeneratorAgent(num_variants=3),
        consensus_engine=ConsensusEngineAgent(),
        creative_memory=FileSystemCreativeMemory(),
        feedback_collector=feedback_collector,
        feedback_learner=SimpleFeedbackLearner(feedback_collector),
        feature_store=fs,
        profiler=PerformanceProfiler(),
        memory_manager=MemoryManager(),
        checkpoint_manager=CheckpointManager(),
        pipeline_cache=PipelineCache(),
        observability=ObservabilityStack(),
        versioning=VersioningManager(),
    )

    assert orchestrator is not None
    assert orchestrator.profiler is not None
    assert orchestrator.memory_manager is not None
    assert orchestrator.checkpoint_manager is not None
    assert orchestrator.pipeline_cache is not None
    assert orchestrator.observability is not None
    assert orchestrator.versioning is not None
