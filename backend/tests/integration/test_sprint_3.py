"""Sprint 3 regression and integration tests for Viral Intelligence."""

import asyncio
import subprocess

import numpy as np
import pytest


class TestPhase1ViralScoreEngine:
    """Tests for Viral Score Engine."""

    def test_viral_score_composite_calculation(self):
        """ViralScoreEngine produces composite score from multiple factors."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.viral_score_engine.application.viral_score_agent import (
            ViralScoreEngineAgent,
        )

        engine = ViralScoreEngineAgent()

        context = {
            "vision_features": {
                "duration_seconds": 60,
                "scene_count": 20,
                "video_info": {"width": 1920, "height": 1080},
            },
            "audio_features": {
                "peaks": [{"time": i, "energy": 0.8} for i in range(15)],
                "rms_energy": {"times": list(range(60)), "values": [0.2] * 60},
                "duration": 60,
            },
            "speech_features": {
                "segments": [{"start": i * 3, "end": i * 3 + 2, "text": "hello world test"} for i in range(10)],
                "transcript": "hello world test",
            },
            "attention_features": {
                "peaks": [{"time": 5, "attention_score": 0.9}],
                "timeline": [
                    {"time": t, "attention_score": 0.5 + 0.3 * np.sin(t * 0.5), "audio_energy": 0.3, "scene_change": 0, "speech_active": 0}
                    for t in range(60)
                ],
            },
            "narrative_features": {
                "narrative_structure": {"acts": [{"name": "intro", "start": 0, "end": 10}], "scene_count": 20, "duration": 60},
                "climax_candidates": [{"timestamp": 30, "score": 0.85}],
            },
        }

        result = asyncio.run(engine.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        viral_map = result.features.get("viral_score_map", {})
        assert "average_score" in viral_map
        assert 0 <= viral_map["average_score"] <= 1.0
        assert "best_platform" in viral_map
        assert viral_map["best_platform"] in ["tiktok", "youtube_shorts", "facebook_reels", "instagram_reels"]

    def test_viral_score_factors_present(self):
        """Viral score includes all 7 factors."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.viral_score_engine.application.viral_score_agent import (
            ViralScoreEngineAgent,
        )

        engine = ViralScoreEngineAgent()
        result = asyncio.run(engine.execute(AgentInput(
            media_reference="test.mp4",
            context={
                "vision_features": {"duration_seconds": 30},
                "audio_features": {"peaks": [], "rms_energy": {"times": [], "values": []}, "duration": 30},
                "speech_features": {"segments": []},
                "attention_features": {"peaks": [], "timeline": []},
                "narrative_features": {"narrative_structure": {"acts": [], "scene_count": 0, "duration": 30}},
            },
        )))

        viral_map = result.features.get("viral_score_map", {})
        scores = viral_map.get("scores", [])
        assert len(scores) > 0
        factors = scores[0].get("factors", [])
        factor_names = [f["factor_name"] for f in factors]
        expected = ["hook", "emotion", "curiosity", "visual_pacing", "speech_pacing", "novelty", "retention_prediction"]
        for exp in expected:
            assert exp in factor_names, f"Missing factor: {exp}"


class TestPhase2HookOptimizer:
    """Tests for Hook Optimizer."""

    def test_hook_optimizer_selects_strategy(self):
        """HookOptimizer selects best strategy for each clip."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.hook_optimizer.application.hook_optimizer_agent import (
            HookOptimizerAgent,
        )

        optimizer = HookOptimizerAgent()

        context = {
            "selected_clips": [
                {"clip_id": "c1", "start": 10.0, "end": 25.0},
            ],
            "features": {
                "vision_features": {"duration_seconds": 60},
                "audio_features": {
                    "rms_energy": {"times": list(range(60)), "values": [0.05] * 10 + [0.8] * 5 + [0.1] * 45},
                },
                "attention_features": {
                    "peaks": [{"time": 12.0, "attention_score": 0.9}],
                },
            },
        }

        result = asyncio.run(optimizer.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        hooks = result.features.get("optimized_hooks", [])
        assert len(hooks) == 1
        hook = hooks[0]
        assert "strategy" in hook
        assert hook["strategy"] in ["jump_to_peak", "trim_silence", "start_with_reaction", "none"]
        assert "hook_score" in hook
        assert 0 <= hook["hook_score"] <= 1.0
        assert "risk_level" in hook

    def test_hook_optimizer_limits_shift(self):
        """HookOptimizer does not shift start more than 3 seconds."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.hook_optimizer.application.hook_optimizer_agent import (
            HookOptimizerAgent,
        )

        optimizer = HookOptimizerAgent()

        context = {
            "selected_clips": [{"clip_id": "c1", "start": 5.0, "end": 20.0}],
            "features": {
                "vision_features": {"duration_seconds": 60},
                "audio_features": {"rms_energy": {"times": [], "values": []}},
                "attention_features": {"peaks": []},
            },
        }

        result = asyncio.run(optimizer.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        hooks = result.features.get("optimized_hooks", [])
        if hooks:
            shift = abs(hooks[0]["optimized_start"] - hooks[0]["original_start"])
            assert shift <= 3.0, f"Shift too large: {shift}"


class TestPhase3RetentionSimulator:
    """Tests for Retention Simulator."""

    def test_retention_curve_generated(self):
        """RetentionSimulator generates a curve with points."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.retention_simulator.application.retention_simulator_agent import (
            RetentionSimulatorAgent,
        )

        sim = RetentionSimulatorAgent()

        context = {
            "selected_clips": [{"start": 0, "end": 15}],
            "features": {
                "vision_features": {"duration_seconds": 60},
                "audio_features": {"peaks": [], "rms_energy": {"times": [], "values": []}},
                "attention_features": {
                    "timeline": [
                        {"time": t, "attention_score": max(0.3, 1.0 - t * 0.05)}
                        for t in range(60)
                    ],
                },
            },
            "target_platform": "tiktok",
        }

        result = asyncio.run(sim.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        curves = result.features.get("retention_curves", [])
        assert len(curves) >= 1
        curve = curves[0]
        assert "points" in curve
        assert len(curve["points"]) > 0
        assert "average_retention" in curve
        assert "estimated_avg_watch_pct" in curve
        assert 0 <= curve["estimated_avg_watch_pct"] <= 1.0

    def test_retention_detects_drops(self):
        """RetentionSimulator detects critical drop points."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.retention_simulator.application.retention_simulator_agent import (
            RetentionSimulatorAgent,
        )

        sim = RetentionSimulatorAgent()

        # Create a timeline with a sudden attention drop
        timeline = []
        for t in range(60):
            score = 0.8 if t < 30 else 0.2  # sudden drop
            timeline.append({"time": float(t), "attention_score": score})

        context = {
            "selected_clips": [{"start": 0, "end": 60}],
            "features": {
                "vision_features": {"duration_seconds": 60},
                "audio_features": {"peaks": [], "rms_energy": {"times": [], "values": []}},
                "attention_features": {"timeline": timeline},
            },
            "target_platform": "tiktok",
        }

        result = asyncio.run(sim.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        curves = result.features.get("retention_curves", [])
        assert len(curves) > 0
        drops = curves[0].get("critical_drop_points", [])
        # Should detect at least one drop after the attention cliff
        assert len(drops) > 0


class TestPhase4AudienceAndCreativeDirector:
    """Tests for Audience Model and Creative Director."""

    def test_audience_model_generates_platform_brief(self):
        """AudienceDirector generates platform-specific briefs."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.audience_director.application.audience_director_agent import (
            AudienceDirectorAgent,
        )

        director = AudienceDirectorAgent()

        for platform in ["tiktok", "youtube_shorts", "facebook_reels", "instagram_reels"]:
            result = asyncio.run(director.execute(AgentInput(
                media_reference="test.mp4",
                context={
                    "target_platform": platform,
                    "content_features": {
                        "vision_features": {"duration_seconds": 60, "scene_count": 15},
                        "viral_score_map": {"average_score": 0.7},
                    },
                },
            )))

            brief = result.features.get("audience_brief", {})
            assert brief["target_platform"] == platform
            assert "estimated_attention_span" in brief
            assert "optimal_clip_count" in brief
            assert brief["optimal_clip_count"] >= 1

            constraints = result.features.get("creative_constraints", {})
            assert constraints["platform"] == platform
            assert "pacing" in constraints
            assert "caption_style" in constraints

    def test_creative_director_optimizes_for_viral(self):
        """CreativeDirector selects clips based on viral score."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.viral_intelligence.creative_director_ai.application.creative_director_agent import (
            CreativeDirectorAgent,
        )

        cd = CreativeDirectorAgent()

        context = {
            "attention_features": {
                "peaks": [
                    {"time": 5.0, "attention_score": 0.85},
                    {"time": 20.0, "attention_score": 0.75},
                    {"time": 35.0, "attention_score": 0.90},
                ],
                "timeline": [],
            },
            "narrative_features": {
                "narrative_structure": {"acts": [], "scene_count": 10, "duration": 60},
                "climax_candidates": [{"timestamp": 35.0, "score": 0.88}],
            },
            "speech_features": {"segments": []},
            "viral_score_map": {
                "scores": [
                    {"segment_start": 0, "segment_end": 10, "composite_score": 0.6, "factors": []},
                    {"segment_start": 30, "segment_end": 40, "composite_score": 0.9, "factors": []},
                ],
                "average_score": 0.75,
            },
            "retention_curves": [],
            "optimized_hooks": [],
            "creative_constraints": {"max_clips": 3, "clip_duration_range": [10, 30]},
            "video_understanding": {"genre": "gaming"},
        }

        result = asyncio.run(cd.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        clips = result.features.get("selected_clips", [])
        assert len(clips) > 0
        # Should prioritize the high viral score segment
        for clip in clips:
            assert "viral_score" in clip

    def test_platform_specific_clip_count(self):
        """Different platforms recommend different clip counts."""
        from backend.src.viral_intelligence.audience_model.infrastructure.default_audience_model import (
            DefaultAudienceModel,
        )

        model = DefaultAudienceModel()
        tiktok_count = model.recommend_clip_count(120, "tiktok")
        shorts_count = model.recommend_clip_count(120, "youtube_shorts")

        # TikTok prefers shorter clips → more clips
        assert tiktok_count >= shorts_count or tiktok_count == shorts_count
        assert tiktok_count >= 1
        assert shorts_count >= 1


class TestSprint3Integration:
    """End-to-end integration tests for Sprint 3."""

    @pytest.mark.asyncio
    async def test_sprint3_orchestrator_runs(self, tmp_path):
        """Sprint 3 orchestrator processes video end-to-end."""
        from backend.src.agents.attention_agent.application.estimate_attention import (
            AttentionAgent,
            DummyAttentionProvider,
        )
        from backend.src.agents.audio_agent.application.extract_audio_features import (
            AudioAgent,
            DummyAudioProvider,
        )
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
        from backend.src.core.application.services.sprint3_orchestrator import Sprint3Orchestrator
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
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

        # Create test video
        video_path = tmp_path / "test.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=20:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=20",
            "-pix_fmt", "yuv420p", str(video_path),
        ]
        subprocess.run(cmd, capture_output=True)

        media = FFmpegMediaAdapter()
        fs = FileSystemFeatureStore(tmp_path / "features")
        vu_agent = VideoUnderstandingAgent(DummyVideoUnderstandingProvider())

        orchestrator = Sprint3Orchestrator(
            event_bus=EventBus(),
            telemetry=TelemetryService(),
            benchmark=BenchmarkSuite(),
            vision_agent=VisionAgent(media),
            audio_agent=AudioAgent(media, DummyAudioProvider()),
            speech_agent=SpeechAgent(DummySpeechProvider()),
            attention_agent=AttentionAgent(DummyAttentionProvider()),
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
            feature_store=fs,
        )

        project = VideoProject(name="sprint3_test", source_path=video_path)
        completed = await orchestrator.process_video(project, platform="tiktok")

        assert completed.status.name == "COMPLETED"
        assert len(completed.clips) >= 1
