"""Golden Dataset for regression testing and continuous validation."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.src.infrastructure.config.settings import settings


class GoldenDataset:
    """Curated dataset of test videos with expected outcomes."""

    VIDEOS = [
        {"name": "gaming_short", "duration": 15, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "tiktok"},
        {"name": "podcast_segment", "duration": 30, "width": 1280, "height": 720, "expected_clips": 1, "expected_platform": "youtube_shorts"},
        {"name": "sports_highlight", "duration": 20, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "tiktok"},
        {"name": "tutorial_clip", "duration": 25, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "youtube_shorts"},
        {"name": "music_moment", "duration": 18, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "tiktok"},
        {"name": "news_bite", "duration": 12, "width": 1280, "height": 720, "expected_clips": 1, "expected_platform": "tiktok"},
        {"name": "comedy_sketch", "duration": 22, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "tiktok"},
        {"name": "travel_vlog", "duration": 28, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "instagram_reels"},
        {"name": "cooking_show", "duration": 35, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "youtube_shorts"},
        {"name": "anime_scene", "duration": 16, "width": 1920, "height": 1080, "expected_clips": 1, "expected_platform": "tiktok"},
    ]

    @classmethod
    def generate_videos(cls, output_dir: Path) -> list[Path]:
        """Generate the golden dataset videos."""
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []

        for spec in cls.VIDEOS:
            path = output_dir / f"{spec['name']}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={spec['duration']}:size={spec['width']}x{spec['height']}:rate=30",
                "-f", "lavfi", "-i", f"sine=frequency=1000:duration={spec['duration']}",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                paths.append(path)

        return paths

    @classmethod
    def get_expected(cls, video_name: str) -> dict[str, Any]:
        """Get expected output for a golden video."""
        for spec in cls.VIDEOS:
            if spec["name"] == video_name:
                return {
                    "expected_clips": spec["expected_clips"],
                    "expected_platform": spec["expected_platform"],
                    "expected_resolution": "1080x1920",
                    "expected_codec": "h264",
                }
        return {}


class TestGoldenDataset:
    """Regression tests using the golden dataset."""

    @pytest.fixture(scope="class")
    def golden_videos(self, tmp_path_factory):
        """Generate golden dataset."""
        output_dir = tmp_path_factory.mktemp("golden")
        return GoldenDataset.generate_videos(output_dir)

    @pytest.mark.asyncio
    async def test_golden_videos_produce_clips(self, golden_videos):
        """Each golden video must produce at least the expected number of clips."""
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.checkpoint.checkpoint_manager import CheckpointManager
        from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache
        from backend.src.infrastructure.config_profiles.config_profile_manager import ConfigProfileManager
        from backend.src.infrastructure.versioning.versioning_manager import VersioningManager

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

        media = FFmpegMediaAdapter()
        fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
        feedback_collector = FileSystemFeedbackCollector()

        # Use balanced profile for golden tests
        profile_mgr = ConfigProfileManager()
        profile = profile_mgr.get_profile("balanced")

        # Versioning
        vm = VersioningManager()
        vm.auto_detect_versions()

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
            candidate_generator=MultiCandidateGeneratorAgent(num_variants=0),  # skip for speed
            consensus_engine=ConsensusEngineAgent(),
            creative_memory=FileSystemCreativeMemory(),
            feedback_collector=feedback_collector,
            feedback_learner=SimpleFeedbackLearner(feedback_collector),
            feature_store=fs,
        )

        results = []
        for video_path in golden_videos:
            expected = GoldenDataset.get_expected(video_path.stem)
            platform = expected.get("expected_platform", "tiktok")

            project = VideoProject(name=video_path.stem, source_path=video_path)
            completed = await orchestrator.process_video(project, platform=platform)

            # Validate
            assert completed.status.name == "COMPLETED", f"{video_path.name} failed"
            assert len(completed.clips) >= expected.get("expected_clips", 1), f"{video_path.name} produced no clips"

            for clip in completed.clips:
                if clip.export_path and clip.export_path.exists():
                    # Verify resolution
                    import subprocess
                    probe = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(clip.export_path)],
                        capture_output=True, text=True
                    )
                    if probe.returncode == 0:
                        import json as json_mod
                        data = json_mod.loads(probe.stdout)
                        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
                        if video_stream:
                            assert video_stream.get("width") == 1080
                            assert video_stream.get("height") == 1920
                            assert video_stream.get("codec_name") == "h264"

            # Generate manifest
            vm.generate_manifest(project.project_id, "balanced")

            results.append({
                "video": video_path.name,
                "status": "PASS",
                "clips": len(completed.clips),
                "platform": platform,
            })

        # Save golden report
        report_path = settings.ORION_HOME / "golden_reports" / f"golden_{int(__import__('time').time())}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump({"total": len(results), "passed": len(results), "results": results}, f, indent=2)
