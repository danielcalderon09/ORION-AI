"""Regression test suite with baseline comparison."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.src.infrastructure.config.settings import settings


class RegressionBaseline:
    """Manages expected baseline results for regression testing."""

    BASELINE_DIR = settings.ORION_HOME / "regression_baselines"

    def __init__(self):
        self.BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    def save_baseline(self, test_name: str, metrics: dict[str, Any]) -> Path:
        """Save a new baseline for a test case."""
        path = self.BASELINE_DIR / f"{test_name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        return path

    def load_baseline(self, test_name: str) -> dict[str, Any] | None:
        """Load existing baseline for comparison."""
        path = self.BASELINE_DIR / f"{test_name}.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def compare(self, test_name: str, actual: dict[str, Any]) -> dict[str, Any]:
        """Compare actual results against baseline."""
        baseline = self.load_baseline(test_name)
        if baseline is None:
            return {
                "status": "no_baseline",
                "message": f"No baseline found for {test_name}. Run with --save-baseline first.",
            }

        differences = []
        all_keys = set(baseline.keys()) | set(actual.keys())

        for key in all_keys:
            base_val = baseline.get(key)
            actual_val = actual.get(key)

            if base_val != actual_val:
                # For numeric values, check within tolerance
                if isinstance(base_val, (int, float)) and isinstance(actual_val, (int, float)):
                    tolerance = base_val * 0.15 if base_val != 0 else 0.1  # 15% tolerance
                    if abs(actual_val - base_val) > tolerance:
                        differences.append({
                            "key": key,
                            "baseline": base_val,
                            "actual": actual_val,
                            "tolerance": tolerance,
                        })
                else:
                    differences.append({
                        "key": key,
                        "baseline": base_val,
                        "actual": actual_val,
                    })

        passed = len(differences) == 0
        return {
            "status": "passed" if passed else "failed",
            "test_name": test_name,
            "differences": differences,
            "baseline_path": str(self.BASELINE_DIR / f"{test_name}.json"),
        }


# Pytest fixtures and tests
@pytest.fixture
def baseline():
    return RegressionBaseline()


@pytest.fixture
def sample_video(tmp_path):
    """Create a minimal synthetic test video using FFmpeg."""
    video_path = tmp_path / "test_video.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=30:size=1920x1080:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=30",
        "-pix_fmt", "yuv420p",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"FFmpeg not available or failed: {result.stderr}")
    return video_path


class TestSprint15Regression:
    """Regression tests for Sprint 1.5 capabilities."""

    @pytest.mark.asyncio
    async def test_pipeline_produces_clips(self, sample_video, baseline):
        """Test that the full pipeline produces at least 1 clip."""
        from backend.src.agents.attention_agent.application.estimate_attention import (
            AttentionAgent,
            DummyAttentionProvider,
        )
        from backend.src.agents.audio_agent.application.extract_audio_features import (
            AudioAgent,
            DummyAudioProvider,
        )
        from backend.src.agents.director_agent.application.director_service import DirectorAgent
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
        from backend.src.core.application.services.orchestration_service import OrchestrationService
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
        from backend.src.infrastructure.cognition.knowledge_graph_impl import InMemoryKnowledgeGraph
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService

        media = FFmpegMediaAdapter()
        fs = FileSystemFeatureStore(settings.ORION_HOME / "features_test")
        orchestrator = OrchestrationService(
            event_bus=EventBus(),
            telemetry=TelemetryService(),
            benchmark=BenchmarkSuite(),
            vision_agent=VisionAgent(media),
            audio_agent=AudioAgent(media, DummyAudioProvider()),
            speech_agent=SpeechAgent(DummySpeechProvider()),
            attention_agent=AttentionAgent(DummyAttentionProvider()),
            narrative_agent=NarrativeIntelligenceAgent(),
            director_agent=DirectorAgent(),
            dop_agent=DoPAgent(),
            exporter_agent=ExporterAgent(media),
            qa_agent=QAAgent(),
            feature_store=fs,
            knowledge_graph_factory=InMemoryKnowledgeGraph,
        )

        project = VideoProject(
            name="regression_test",
            source_path=sample_video,
        )

        completed = await orchestrator.process_video(project)

        assert completed.status.name == "COMPLETED"
        assert len(completed.clips) >= 1, "Pipeline should produce at least 1 clip"

        # Collect metrics for regression
        metrics = {
            "clip_count": len(completed.clips),
            "has_exports": any(c.export_path and c.export_path.exists() for c in completed.clips),
            "total_clips_exported": sum(1 for c in completed.clips if c.export_path and c.export_path.exists()),
        }

        # Regression comparison
        result = baseline.compare("test_pipeline_produces_clips", metrics)
        if result["status"] == "no_baseline":
            baseline.save_baseline("test_pipeline_produces_clips", metrics)
            pytest.skip("Baseline saved. Run again to compare.")

        assert result["status"] == "passed", f"Regression failed: {result['differences']}"

    def test_qa_rejects_invalid_resolution(self, tmp_path):
        """Test that QA agent catches wrong resolution."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.agents.qa_agent.application.qa_service import BasicQAProvider, QAAgent

        # Create a fake wrong-resolution video
        wrong_video = tmp_path / "wrong_res.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-pix_fmt", "yuv420p",
            str(wrong_video),
        ]
        subprocess.run(cmd, capture_output=True)

        qa = QAAgent(qa_provider=BasicQAProvider())

        import asyncio
        result = asyncio.run(qa.execute(AgentInput(
            media_reference=str(wrong_video),
            context={
                "expected_params": {
                    "width": 1080,
                    "height": 1920,
                    "video_codec": "libx264",
                    "format": "mp4",
                },
            },
        )))

        assert result.features["validation"]["passed"] is False
        res_check = next((c for c in result.features["validation"]["checks"] if c["name"] == "resolution"), None)
        assert res_check is not None
        assert res_check["passed"] is False

    def test_confidence_score_computed(self):
        """Test that DirectorAgent computes confidence for each clip."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.agents.director_agent.application.director_service import DirectorAgent

        director = DirectorAgent()

        context = {
            "attention_features": {
                "peaks": [{"time": 5.0, "attention_score": 0.9}],
                "valleys": [],
                "timeline": [
                    {"time": 4.0, "attention_score": 0.3},
                    {"time": 5.0, "attention_score": 0.9},
                    {"time": 6.0, "attention_score": 0.4},
                ],
            },
            "narrative_features": {
                "narrative_structure": {
                    "acts": [
                        {"name": "introduction", "start": 0, "end": 3},
                        {"name": "development", "start": 3, "end": 8},
                    ],
                    "scene_count": 5,
                    "duration": 30,
                },
                "climax_candidates": [{"timestamp": 5.0, "score": 0.85}],
            },
            "speech_features": {"segments": []},
            "vision_features": {"duration_seconds": 30, "video_info": {"width": 1920, "height": 1080}},
        }

        import asyncio
        result = asyncio.run(director.execute(AgentInput(
            media_reference="dummy.mp4",
            context=context,
        )))

        clips = result.features["selected_clips"]
        assert len(clips) > 0
        for clip in clips:
            assert "confidence" in clip, "Each clip must have confidence score"
            assert "composite" in clip["confidence"], "Confidence must have composite score"
            assert 0 <= clip["confidence"]["composite"] <= 1, "Composite confidence must be 0-1"

    def test_debug_timeline_generated(self):
        """Test that debug mode produces timeline data."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.agents.director_agent.application.director_service import DirectorAgent

        director = DirectorAgent()

        context = {
            "debug_mode": True,
            "attention_features": {
                "peaks": [{"time": 5.0, "attention_score": 0.9}],
                "valleys": [],
                "timeline": [
                    {"time": 4.0, "attention_score": 0.3, "audio_energy": 0.2, "scene_change": 0, "speech_active": 0},
                    {"time": 5.0, "attention_score": 0.9, "audio_energy": 0.8, "scene_change": 1, "speech_active": 0},
                    {"time": 6.0, "attention_score": 0.4, "audio_energy": 0.3, "scene_change": 0, "speech_active": 0},
                ],
            },
            "narrative_features": {
                "narrative_structure": {
                    "acts": [{"name": "intro", "start": 0, "end": 3}],
                    "scene_count": 3,
                    "duration": 30,
                },
                "climax_candidates": [{"timestamp": 5.0, "score": 0.85}],
            },
            "speech_features": {
                "segments": [{"start": 4.5, "end": 5.5, "text": "hello world"}],
            },
            "vision_features": {"duration_seconds": 30, "video_info": {"width": 1920, "height": 1080}},
        }

        import asyncio
        result = asyncio.run(director.execute(AgentInput(
            media_reference="dummy.mp4",
            context=context,
        )))

        debug_timeline = result.features.get("debug_timeline")
        assert debug_timeline is not None, "Debug mode must produce timeline data"
        assert "points" in debug_timeline, "Timeline must have points"
        assert "clips" in debug_timeline, "Timeline must have clips"
        assert "summary" in debug_timeline, "Timeline must have summary"
        assert len(debug_timeline["points"]) > 0, "Timeline must have data points"

    def test_feature_store_persistence(self, tmp_path):
        """Test that features are saved and retrievable."""
        from uuid import uuid4

        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore

        store = FileSystemFeatureStore(tmp_path)
        project_id = uuid4()
        test_data = {"test": "data", "values": [1, 2, 3]}

        store.save(project_id, "test_agent", "test_feature", test_data, version="1.0")
        loaded = store.load(project_id, "test_agent", "test_feature", version="1.0")

        assert loaded == test_data
        assert store.exists(project_id, "test_agent", "test_feature", version="1.0")

    def test_capability_registry_resolution(self):
        """Test that capability registry resolves providers correctly."""
        from backend.src.infrastructure.model_registry.capability_registry import (
            CapabilityRegistry,
            ModelMetadata,
        )

        registry = CapabilityRegistry()
        registry.register(ModelMetadata(
            model_id="test_model",
            capability="test_capability",
            provider_class=dict,
            version="1.0",
            description="Test",
            requirements=[],
            default=True,
        ))

        provider = registry.resolve("test_capability")
        assert provider is dict

        instance = registry.get_instance("test_capability")
        assert isinstance(instance, dict)
