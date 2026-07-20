"""Golden Dataset for regression testing and continuous validation."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.src.infrastructure.config.settings import settings


class GoldenAttentionProvider:
    """Deterministic attention signal for the synthetic contract dataset."""

    async def estimate_attention(self, features: dict[str, Any]) -> list[dict]:
        del features
        return [
            {
                "time": 2.0,
                "attention_score": 0.9,
                "audio_energy": 0.8,
                "scene_change": 0.8,
                "speech_active": 0.0,
            },
            {
                "time": 7.0,
                "attention_score": 0.5,
                "audio_energy": 0.5,
                "scene_change": 0.0,
                "speech_active": 0.0,
            },
        ]


class GoldenAudioProvider:
    """Deterministic audio features without model/JIT dependencies."""

    async def extract_features(self, audio_path: Path) -> dict[str, Any]:
        assert audio_path.is_file()
        return {
            "rms_energy": {"times": [0.0, 2.0, 7.0], "values": [0.2, 0.8, 0.4]},
            "onset_strength": {"times": [0.0, 2.0], "values": [0.1, 0.8]},
            "peaks": [{"time": 2.0, "energy": 0.8}],
            "duration": 10.0,
            "sample_rate": 16000,
        }


class GoldenDataset:
    """Curated dataset of test videos with expected outcomes."""

    VIDEOS = [
        {
            "name": "gaming_short",
            "duration": 15,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "tiktok",
        },
        {
            "name": "podcast_segment",
            "duration": 30,
            "width": 1280,
            "height": 720,
            "expected_clips": 1,
            "expected_platform": "youtube_shorts",
        },
        {
            "name": "sports_highlight",
            "duration": 20,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "tiktok",
        },
        {
            "name": "tutorial_clip",
            "duration": 25,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "youtube_shorts",
        },
        {
            "name": "music_moment",
            "duration": 18,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "tiktok",
        },
        {
            "name": "news_bite",
            "duration": 12,
            "width": 1280,
            "height": 720,
            "expected_clips": 1,
            "expected_platform": "tiktok",
        },
        {
            "name": "comedy_sketch",
            "duration": 22,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "tiktok",
        },
        {
            "name": "travel_vlog",
            "duration": 28,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "instagram_reels",
        },
        {
            "name": "cooking_show",
            "duration": 35,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "youtube_shorts",
        },
        {
            "name": "anime_scene",
            "duration": 16,
            "width": 1920,
            "height": 1080,
            "expected_clips": 1,
            "expected_platform": "tiktok",
        },
    ]

    @classmethod
    def generate_videos(cls, output_dir: Path) -> list[Path]:
        """Generate the golden dataset videos."""
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []

        for spec in cls.VIDEOS:
            path = output_dir / f"{spec['name']}.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=duration={spec['duration']}:size={spec['width']}x{spec['height']}:rate=5",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=1000:duration={spec['duration']}",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(path),
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"golden fixture generation timed out: {spec['name']}") from exc
            if result.returncode != 0:
                raise RuntimeError(
                    f"golden fixture generation failed: {spec['name']}: {result.stderr[-500:]}"
                )
            paths.append(path)

        if len(paths) != len(cls.VIDEOS):
            raise RuntimeError("golden fixture generation did not produce the complete dataset")

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

    @pytest.fixture
    def golden_videos(self, tmp_path, monkeypatch):
        """Generate the dataset and isolate every runtime write."""
        orion_home = tmp_path / "orion-home"
        projects_dir = tmp_path / "projects"
        temp_dir = tmp_path / "temp"
        models_dir = tmp_path / "models"
        for directory in (orion_home, projects_dir, temp_dir, models_dir):
            directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, "ORION_HOME", orion_home)
        monkeypatch.setattr(settings, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(settings, "TEMP_DIR", temp_dir)
        monkeypatch.setattr(settings, "MODELS_DIR", models_dir)
        output_dir = tmp_path / "golden"
        return GoldenDataset.generate_videos(output_dir)

    @pytest.mark.asyncio
    async def test_golden_videos_produce_clips(self, golden_videos):
        """Each golden video must produce at least the expected number of clips."""
        from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
        from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
        from backend.src.agents.dop_agent.application.dop_service import DoPAgent
        from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
        from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import (
            NarrativeIntelligenceAgent,
        )
        from backend.src.agents.qa_agent.application.qa_service import QAAgent
        from backend.src.agents.speech_agent.application.transcribe_speech import (
            DummySpeechProvider,
            SpeechAgent,
        )
        from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
        from backend.src.cognition.video_understanding.application.video_understanding_agent import (
            VideoUnderstandingAgent,
        )
        from backend.src.cognition.video_understanding.i_video_understanding_provider import (
            DummyVideoUnderstandingProvider,
        )
        from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
        from backend.src.infrastructure.versioning.versioning_manager import VersioningManager
        from backend.src.sprint4.consensus_engine.application.consensus_engine_agent import (
            ConsensusEngineAgent,
        )
        from backend.src.sprint4.creative_memory.infrastructure.file_system_creative_memory import (
            FileSystemCreativeMemory,
        )
        from backend.src.sprint4.critic_ai.application.critic_ai_agent import CriticAIAgent
        from backend.src.sprint4.human_feedback.infrastructure.file_system_feedback import (
            FileSystemFeedbackCollector,
            SimpleFeedbackLearner,
        )
        from backend.src.sprint4.multi_candidate_generator.application.multi_candidate_generator_agent import (
            MultiCandidateGeneratorAgent,
        )
        from backend.src.sprint4.reflection_engine.application.reflection_engine_agent import (
            ReflectionEngineAgent,
        )
        from backend.src.viral_intelligence.audience_director.application.audience_director_agent import (
            AudienceDirectorAgent,
        )
        from backend.src.viral_intelligence.creative_director_ai.application.creative_director_agent import (
            CreativeDirectorAgent,
        )
        from backend.src.viral_intelligence.hook_optimizer.application.hook_optimizer_agent import (
            HookOptimizerAgent,
        )
        from backend.src.viral_intelligence.retention_simulator.application.retention_simulator_agent import (
            RetentionSimulatorAgent,
        )
        from backend.src.viral_intelligence.viral_score_engine.application.viral_score_agent import (
            ViralScoreEngineAgent,
        )

        media = FFmpegMediaAdapter()
        fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
        feedback_collector = FileSystemFeedbackCollector()

        # Versioning
        vm = VersioningManager()
        vm.auto_detect_versions()

        orchestrator = Sprint5Orchestrator(
            event_bus=EventBus(),
            telemetry=TelemetryService(),
            benchmark=BenchmarkSuite(),
            vision_agent=VisionAgent(media),
            audio_agent=AudioAgent(media, GoldenAudioProvider()),
            speech_agent=SpeechAgent(DummySpeechProvider()),
            attention_agent=AttentionAgent(GoldenAttentionProvider()),
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
            assert len(completed.clips) >= expected.get("expected_clips", 1), (
                f"{video_path.name} produced no clips"
            )

            for clip in completed.clips:
                if clip.export_path and clip.export_path.exists():
                    # Verify resolution
                    import subprocess

                    probe = subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "quiet",
                            "-print_format",
                            "json",
                            "-show_streams",
                            str(clip.export_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if probe.returncode == 0:
                        import json as json_mod

                        data = json_mod.loads(probe.stdout)
                        video_stream = next(
                            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
                            None,
                        )
                        if video_stream:
                            assert video_stream.get("width") == 1080
                            assert video_stream.get("height") == 1920
                            assert video_stream.get("codec_name") == "h264"

            # Generate manifest
            manifest = vm.create_reproducibility_manifest(
                video_path,
                Sprint5Orchestrator.PIPELINE_VERSION,
                platform,
            )
            assert manifest.target_platform == platform

            results.append(
                {
                    "video": video_path.name,
                    "status": "PASS",
                    "clips": len(completed.clips),
                    "platform": platform,
                }
            )

        # Save golden report
        report_path = (
            settings.ORION_HOME / "golden_reports" / f"golden_{int(__import__('time').time())}.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(
                {"total": len(results), "passed": len(results), "results": results}, f, indent=2
            )
