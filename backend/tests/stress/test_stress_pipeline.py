"""Stress tests for production hardening."""

import asyncio
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from backend.src.infrastructure.config.settings import settings


class TestStressPipeline:
    """Stress tests measuring throughput, failure rate, and resource usage."""

    @pytest.fixture(scope="class")
    def stress_videos(self, tmp_path_factory):
        """Generate a batch of synthetic stress test videos."""
        videos = []
        batch_dir = tmp_path_factory.mktemp("stress_videos")
        durations = [10, 30, 60, 120, 300]  # seconds
        resolutions = [(640, 480), (1280, 720), (1920, 1080)]

        for i, (dur, (w, h)) in enumerate([(d, r) for d in durations for r in resolutions]):
            path = batch_dir / f"stress_{i:02d}_{dur}s_{w}x{h}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={dur}:size={w}x{h}:rate=30",
                "-f", "lavfi", "-i", f"sine=frequency=1000:duration={dur}",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                videos.append({"path": path, "duration": dur, "resolution": f"{w}x{h}"})

        return videos

    @pytest.mark.asyncio
    async def test_batch_processing_throughput(self, stress_videos):
        """Process multiple videos and measure throughput."""
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.profiler.performance_profiler import PerformanceProfiler
        from backend.src.infrastructure.memory_manager.memory_manager import MemoryManager, MemoryBudget
        from backend.src.infrastructure.checkpoint.checkpoint_manager import CheckpointManager
        from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache
        from backend.src.infrastructure.config_profiles.config_profile_manager import ConfigProfileManager

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

        import time
        start_time = time.time()

        media = FFmpegMediaAdapter()
        fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
        feedback_collector = FileSystemFeedbackCollector()

        # Use fast profile for stress test
        profile_mgr = ConfigProfileManager()
        fast_profile = profile_mgr.get_profile("fast")

        orchestrator = Sprint5Orchestrator(
            event_bus=EventBus(),
            telemetry=TelemetryService(),
            benchmark=BenchmarkSuite(),
            vision_agent=VisionAgent(media),
            audio_agent=AudioAgent(media),
            speech_agent=SpeechAgent(),
            attention_agent=AttentionAgent(),
            narrative_agent=NarrativeIntelligenceAgent(),
            video_understanding_agent=VideoUnderstandingAgent(DummyVideoUnderstandingProvider()),
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
            candidate_generator=MultiCandidateGeneratorAgent(num_variants=0),  # skip candidates for speed
            consensus_engine=ConsensusEngineAgent(),
            creative_memory=FileSystemCreativeMemory(),
            feedback_collector=feedback_collector,
            feedback_learner=SimpleFeedbackLearner(feedback_collector),
            feature_store=fs,
        )

        success_count = 0
        failure_count = 0
        results = []

        for video_info in stress_videos[:5]:  # Process top 5 for speed
            try:
                project = VideoProject(name=f"stress_{video_info['path'].stem}", source_path=video_info["path"])
                completed = await orchestrator.process_video(project, platform="tiktok")
                if completed.status.name == "COMPLETED":
                    success_count += 1
                else:
                    failure_count += 1
                results.append({
                    "video": video_info["path"].name,
                    "status": completed.status.name,
                    "clips": len(completed.clips),
                })
            except Exception as e:
                failure_count += 1
                results.append({"video": video_info["path"].name, "status": "FAILED", "error": str(e)})

        elapsed = time.time() - start_time
        throughput = len(results) / (elapsed / 3600) if elapsed > 0 else 0

        # Assertions
        assert success_count > 0, "At least some videos must succeed"
        failure_rate = failure_count / max(len(results), 1)
        assert failure_rate < 0.5, f"Failure rate too high: {failure_rate:.0%}"

        # Save stress report
        report = {
            "total_videos": len(results),
            "success": success_count,
            "failure": failure_count,
            "failure_rate": failure_rate,
            "elapsed_sec": elapsed,
            "throughput_videos_per_hour": throughput,
            "results": results,
        }
        import json
        report_path = settings.ORION_HOME / "stress_reports" / f"stress_{int(time.time())}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

    def test_memory_stability(self):
        """Verify memory usage stays within budget during processing."""
        from backend.src.infrastructure.memory_manager.memory_manager import MemoryManager, MemoryBudget

        budget = MemoryBudget(max_ram_mb=2048)
        mm = MemoryManager(budget)

        # Simulate processing
        for i in range(100):
            buf = mm.get_buffer(f"stage_{i % 5}")
            # Simulate frame buffer
            import numpy as np
            dummy_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            buf._add(i, dummy_frame)

        status = mm.get_status()
        assert status["ram_percent"] < 1.0, "RAM should not exceed budget"

        # Evict stages
        for i in range(5):
            mm.evict_stage(f"stage_{i}")
        mm.force_gc()

        status_after = mm.get_status()
        assert status_after["ram_percent"] <= status["ram_percent"], "Memory should decrease after eviction"

    def test_checkpoint_recovery(self):
        """Verify checkpoints can be saved and recovered."""
        from backend.src.infrastructure.checkpoint.checkpoint_manager import CheckpointManager
        from uuid import uuid4

        cp = CheckpointManager()
        pid = uuid4()

        # Save checkpoint
        path = cp.save_checkpoint(pid, "attention", 3, {"status": "ok"}, {"vision": "/tmp/v.json"})
        assert path.exists()

        # Recover
        recovery = cp.get_recovery_point(pid)
        assert recovery is not None
        stage_name, stage_index, brain = recovery
        assert stage_name == "attention"
        assert stage_index == 3
        assert brain["status"] == "ok"

        # Clean old
        cp.clean_old_checkpoints(pid, keep_last=1)
        checkpoints = cp.list_checkpoints(pid)
        assert len(checkpoints) <= 1

    def test_pipeline_cache_hit(self):
        """Verify cache returns hit for identical inputs."""
        from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache

        cache = PipelineCache()
        file_hash = "abc123"
        stage = "vision"
        config = "v1.0"

        data = {"frames": [1, 2, 3], "scenes": 5}
        cache.put(file_hash, stage, config, data)

        hit = cache.get(file_hash, stage, config)
        assert hit is not None
        assert hit["scenes"] == 5

        miss = cache.get(file_hash, stage, "v2.0")
        assert miss is None

        stats = cache.get_stats()
        assert stats["entries"] >= 1

    def test_config_profiles(self):
        """Verify all predefined profiles load correctly."""
        from backend.src.infrastructure.config_profiles.config_profile_manager import ConfigProfileManager

        mgr = ConfigProfileManager()
        profiles = ["fast", "balanced", "quality", "gaming", "podcast", "sports", "anime"]

        for name in profiles:
            profile = mgr.get_profile(name)
            assert profile.name == name
            assert len(profile.agents_enabled) > 0
            assert profile.timeout_seconds > 0
            assert profile.memory_budget_mb > 0

    def test_custom_profile_inheritance(self):
        """Verify custom profiles inherit and override base."""
        from backend.src.infrastructure.config_profiles.config_profile_manager import ConfigProfileManager

        mgr = ConfigProfileManager()
        custom = mgr.create_custom("balanced", {"timeout_seconds": 1200, "target_clip_count": 10})

        assert custom.timeout_seconds == 1200
        assert custom.target_clip_count == 10
        assert custom.name.startswith("custom_balanced_")

    def test_versioning_manifest(self):
        """Verify reproducibility manifest generation."""
        from backend.src.infrastructure.versioning.versioning_manager import VersioningManager

        vm = VersioningManager()
        vm.auto_detect_versions()
        pid = uuid4()
        manifest = vm.generate_manifest(pid, "balanced")

        assert manifest.orion_version is not None
        assert manifest.project_id == str(pid)
        assert manifest.config_profile == "balanced"
        assert len(manifest.components) > 0

        retrieved = vm.get_manifest(pid)
        assert retrieved is not None
        assert retrieved["orion_version"] == manifest.orion_version

    def test_observability_health(self):
        """Verify observability stack produces valid health data."""
        from backend.src.infrastructure.observability.observability_stack import ObservabilityStack

        obs = ObservabilityStack()
        obs.sample_system(active_projects=2, queue_depth=0)
        health = obs.get_health()

        assert "status" in health
        assert health["status"] in ["healthy", "degraded"]
        assert "ram_percent" in health
        assert "disk_free_gb" in health

    def test_agent_metrics_aggregation(self):
        """Verify agent metrics are tracked correctly."""
        from backend.src.infrastructure.observability.observability_stack import ObservabilityStack

        obs = ObservabilityStack()
        for i in range(10):
            obs.record_agent_execution("vision_agent", 1.5 + i * 0.1, success=(i < 9))

        summary = obs.get_agent_summary()
        assert "vision_agent" in summary
        assert summary["vision_agent"]["executions"] == 10
        assert summary["vision_agent"]["failures"] == 1
        assert summary["vision_agent"]["success_rate"] == 0.9
        assert summary["vision_agent"]["avg_duration_sec"] > 0
