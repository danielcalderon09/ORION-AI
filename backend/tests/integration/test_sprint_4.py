"""Sprint 4 regression and integration tests for Auto-Improvement."""

import asyncio
import subprocess

import pytest


class TestPhase1ReflectionAndCritic:
    """Tests for Sprint 4 Phase 1: Reflection Engine + Critic AI."""

    def test_reflection_engine_proposes_improvements(self):
        """ReflectionEngine suggests concrete improvements for a clip."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.sprint4.reflection_engine.application.reflection_engine_agent import (
            ReflectionEngineAgent,
        )

        engine = ReflectionEngineAgent()

        context = {
            "clip": {
                "clip_id": "test_clip_01",
                "start": 10.0,
                "end": 25.0,
                "hook_duration": 3.0,
                "pacing": "slow",
                "subtitle_segments": [],
            },
            "creative_brief": {
                "hook_duration": 1.5,
                "pacing": "fast",
            },
            "metrics": {"quality_score": 0.6, "viral_score": 0.5},
            "retention_curve": {
                "critical_drop_points": [18.0, 22.0],
            },
        }

        result = asyncio.run(engine.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        report = result.features.get("reflection_report", {})
        assert "suggestions" in report
        suggestions = report["suggestions"]
        assert len(suggestions) > 0
        # Should detect hook too long
        hook_suggestions = [s for s in suggestions if s["category"] == "hook"]
        assert len(hook_suggestions) > 0
        # Should detect pacing mismatch
        pacing_suggestions = [s for s in suggestions if s["category"] == "pacing"]
        assert len(pacing_suggestions) > 0
        # Should detect retention drops
        trim_suggestions = [s for s in suggestions if s["category"] == "trim"]
        assert len(trim_suggestions) > 0

    def test_critic_ai_evaluates_multiple_axes(self):
        """CriticAI produces scores for narrative, technical, retention, engagement."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.sprint4.critic_ai.application.critic_ai_agent import CriticAIAgent

        critic = CriticAIAgent()

        candidate = {
            "clip_id": "c1",
            "start": 5.0,
            "end": 20.0,
            "hook_optimized_start": 5.0,
            "viral_score": 0.75,
            "qa_result": {"passed": True, "checks": [{"name": "resolution", "passed": True}]},
            "framing": {"x": 0, "y": 0, "width": 1080, "height": 1920},
        }

        context = {
            "narrative_features": {
                "narrative_structure": {"beats": [{"timestamp": 8.0}, {"timestamp": 12.0}], "duration": 60},
            },
            "attention_features": {"peaks": [{"time": 10.0, "attention_score": 0.9}]},
            "retention_curve": {
                "points": [{"time_offset": 0, "retained_viewers_pct": 1.0}],
                "average_retention": 0.7,
                "estimated_avg_watch_pct": 0.6,
                "critical_drop_points": [],
            },
        }

        result = asyncio.run(critic.execute(AgentInput(
            media_reference="test.mp4",
            context={"candidate": candidate, "context": context},
        )))

        critique = result.features.get("critique_report", {})
        assert "overall_score" in critique
        assert 0 <= critique["overall_score"] <= 1.0
        axis_scores = critique.get("axis_scores", [])
        axes = [a["axis"] for a in axis_scores]
        assert "narrative" in axes
        assert "technical" in axes
        assert "retention" in axes
        assert "engagement" in axes

    def test_critic_detects_qa_failure(self):
        """CriticAI flags fatal flaws when QA fails."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.sprint4.critic_ai.application.critic_ai_agent import CriticAIAgent

        critic = CriticAIAgent()
        candidate = {
            "clip_id": "c_bad",
            "start": 0,
            "end": 5,
            "qa_result": {"passed": False, "checks": [{"name": "resolution", "passed": False}]},
        }

        result = asyncio.run(critic.execute(AgentInput(
            media_reference="test.mp4",
            context={"candidate": candidate, "context": {}},
        )))

        critique = result.features.get("critique_report", {})
        assert len(critique.get("fatal_flaws", [])) > 0
        assert result.features.get("passed") is False


class TestPhase2CandidatesAndConsensus:
    """Tests for Sprint 4 Phase 2: Multi Candidate + Consensus."""

    def test_candidate_generator_produces_variants(self):
        """MultiCandidateGenerator creates multiple variants per clip."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.sprint4.multi_candidate_generator.application.multi_candidate_generator_agent import (
            MultiCandidateGeneratorAgent,
        )

        gen = MultiCandidateGeneratorAgent(num_variants=3)

        context = {
            "selected_clips": [
                {"clip_id": "clip_1", "start": 10.0, "end": 25.0, "viral_score": 0.7},
            ],
            "optimized_hooks": [
                {"clip_id": "clip_1", "optimized_start": 12.0, "strategy": "jump_to_peak"},
            ],
            "creative_constraints": {"pacing": "fast", "caption_style": "animated"},
            "viral_score_map": {},
        }

        result = asyncio.run(gen.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        sets = result.features.get("candidate_sets", [])
        assert len(sets) == 1
        candidates = sets[0]["candidates"]
        assert len(candidates) >= 2  # original + at least one variant
        variant_ids = [c["variant_id"] for c in candidates]
        assert any("original" in v for v in variant_ids)
        assert any("hookopt" in v for v in variant_ids)

    def test_consensus_engine_selects_winner(self):
        """ConsensusEngine picks a winning candidate with confidence."""
        from backend.src.agents.base.i_agent import AgentInput
        from backend.src.sprint4.consensus_engine.application.consensus_engine_agent import (
            ConsensusEngineAgent,
        )

        consensus = ConsensusEngineAgent()

        context = {
            "candidate_sets": [
                {
                    "source_moment_id": "moment_1",
                    "candidates": [
                        {"variant_id": "v1", "parent_clip_id": "c1", "start": 10, "end": 20, "estimated_viral_score": 0.9, "estimated_retention": 0.7},
                        {"variant_id": "v2", "parent_clip_id": "c1", "start": 12, "end": 22, "estimated_viral_score": 0.6, "estimated_retention": 0.5},
                    ],
                }
            ],
            "critiques": [
                {"candidate_id": "v1", "overall_score": 0.85},
                {"candidate_id": "v2", "overall_score": 0.55},
            ],
            "reflections": [
                {"clip_id": "c1", "alignment_score": 0.8},
            ],
            "viral_scores": [],
            "retention_scores": [],
        }

        result = asyncio.run(consensus.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        results = result.features.get("consensus_results", [])
        assert len(results) == 1
        winner = results[0]
        assert winner["winning_candidate_id"] == "v1"  # higher viral score + critique
        assert winner["consensus_confidence"] > 0
        assert "recommended_action" in winner
        assert winner["recommended_action"] in ["accept", "re_generate", "human_review"]


class TestPhase3CreativeMemory:
    """Tests for Sprint 4 Phase 3: Creative Memory."""

    def test_creative_memory_stores_and_retrieves(self, tmp_path):
        """CreativeMemory stores patterns and retrieves by category."""
        # Override path for test
        import backend.src.infrastructure.config.settings as settings_mod
        from backend.src.sprint4.creative_memory.domain.creative_pattern import CreativePattern
        from backend.src.sprint4.creative_memory.infrastructure.file_system_creative_memory import (
            FileSystemCreativeMemory,
        )
        original_orion_home = settings_mod.settings.ORION_HOME
        settings_mod.settings.ORION_HOME = tmp_path

        try:
            memory = FileSystemCreativeMemory()
            pattern = CreativePattern(
                pattern_id="pat_01",
                category="gaming",
                platform="tiktok",
                decision_type="hook",
                context_features={"scene_rate": 0.5},
                action={"start_with_peak": True},
                outcome={"user_rating": 4},
                usage_count=1,
                success_rate=0.8,
            )
            memory.store_pattern(pattern)

            found = memory.find_patterns("gaming", "tiktok")
            assert len(found) == 1
            assert found[0].pattern_id == "pat_01"

            best = memory.get_best_practice("gaming", "tiktok", "hook")
            assert best is not None
            assert best.decision_type == "hook"
        finally:
            settings_mod.settings.ORION_HOME = original_orion_home

    def test_creative_memory_updates_success_rate(self, tmp_path):
        """CreativeMemory updates success rate with new outcomes."""
        import backend.src.infrastructure.config.settings as settings_mod
        original_orion_home = settings_mod.settings.ORION_HOME
        settings_mod.settings.ORION_HOME = tmp_path

        try:
            from backend.src.sprint4.creative_memory.domain.creative_pattern import CreativePattern
            from backend.src.sprint4.creative_memory.infrastructure.file_system_creative_memory import (
                FileSystemCreativeMemory,
            )

            memory = FileSystemCreativeMemory()
            pattern = CreativePattern(
                pattern_id="pat_02", category="podcast", platform="youtube_shorts",
                decision_type="pacing", context_features={}, action={}, outcome={},
                usage_count=1, success_rate=0.5,
            )
            memory.store_pattern(pattern)

            memory.update_outcome("pat_02", {"user_rating": 5})
            updated = memory._patterns["pat_02"]
            assert updated.success_rate > 0.5  # should increase with 5-star rating
            assert updated.usage_count == 2
        finally:
            settings_mod.settings.ORION_HOME = original_orion_home


class TestPhase4HumanFeedback:
    """Tests for Sprint 4 Phase 4: Human Feedback."""

    def test_feedback_collector_records_and_summarizes(self, tmp_path):
        """FeedbackCollector stores feedback and produces summaries."""
        import backend.src.infrastructure.config.settings as settings_mod
        original_orion_home = settings_mod.settings.ORION_HOME
        settings_mod.settings.ORION_HOME = tmp_path

        try:
            from datetime import datetime
            from uuid import uuid4

            from backend.src.sprint4.human_feedback.domain.structured_feedback import (
                StructuredFeedback,
            )
            from backend.src.sprint4.human_feedback.infrastructure.file_system_feedback import (
                FileSystemFeedbackCollector,
            )

            collector = FileSystemFeedbackCollector()
            pid = uuid4()
            feedback = StructuredFeedback(
                feedback_id="fb_01",
                project_id=pid,
                clip_id="clip_1",
                overall_rating=4,
                axis_ratings={"hook": 5, "pacing": 3, "subtitles": 4},
                action_taken="exported",
                freeform_comment="Great clip!",
                created_at=datetime.utcnow(),
                platform="tiktok",
            )
            collector.record_feedback(feedback)

            # Retrieve
            clip_feedback = collector.get_feedback_for_clip("clip_1")
            assert len(clip_feedback) == 1
            assert clip_feedback[0].overall_rating == 4

            # Summary
            summary = collector.get_project_summary(pid)
            assert summary.total_clips == 1
            assert summary.avg_overall_rating == 4.0
            assert summary.export_rate == 1.0
        finally:
            settings_mod.settings.ORION_HOME = original_orion_home

    def test_feedback_learner_suggests_adjustments(self, tmp_path):
        """FeedbackLearner suggests weight adjustments from feedback."""
        import backend.src.infrastructure.config.settings as settings_mod
        original_orion_home = settings_mod.settings.ORION_HOME
        settings_mod.settings.ORION_HOME = tmp_path

        try:
            from datetime import datetime
            from uuid import uuid4

            from backend.src.sprint4.human_feedback.domain.structured_feedback import (
                StructuredFeedback,
            )
            from backend.src.sprint4.human_feedback.infrastructure.file_system_feedback import (
                FileSystemFeedbackCollector,
                SimpleFeedbackLearner,
            )

            collector = FileSystemFeedbackCollector()
            learner = SimpleFeedbackLearner(collector)

            # Add multiple feedbacks with low subtitle ratings
            for i in range(5):
                collector.record_feedback(StructuredFeedback(
                    feedback_id=f"fb_{i}",
                    project_id=uuid4(),
                    clip_id=f"clip_{i}",
                    overall_rating=2,
                    axis_ratings={"subtitles": 1, "hook": 3, "pacing": 3},
                    action_taken="discarded",
                    freeform_comment=None,
                    created_at=datetime.utcnow(),
                    platform="tiktok",
                ))

            adjustments = asyncio.run(learner.suggest_weight_adjustments("gaming", "tiktok"))
            # Since discard rate is high and top issue is subtitles
            assert "subtitle_weight" in adjustments
            assert adjustments["subtitle_weight"] > 1.0
        finally:
            settings_mod.settings.ORION_HOME = original_orion_home


class TestSprint4Integration:
    """End-to-end integration for Sprint 4."""

    @pytest.mark.asyncio
    async def test_sprint4_orchestrator_runs(self, tmp_path):
        """Sprint 4 orchestrator runs full pipeline with auto-improvement."""
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
        from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
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
        feedback_collector = FileSystemFeedbackCollector()

        orchestrator = Sprint5Orchestrator(
            event_bus=EventBus(),
            telemetry=TelemetryService(),
            benchmark=BenchmarkSuite(),
            vision_agent=VisionAgent(media),
            audio_agent=AudioAgent(media, DummyAudioProvider()),
            speech_agent=SpeechAgent(DummySpeechProvider()),
            attention_agent=AttentionAgent(DummyAttentionProvider()),
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
            candidate_generator=MultiCandidateGeneratorAgent(num_variants=3),
            consensus_engine=ConsensusEngineAgent(),
            creative_memory=FileSystemCreativeMemory(),
            feedback_collector=feedback_collector,
            feedback_learner=SimpleFeedbackLearner(feedback_collector),
            feature_store=fs,
        )

        project = VideoProject(name="sprint4_test", source_path=video_path)
        completed = await orchestrator.process_video(project, platform="tiktok")

        assert completed.status.name == "COMPLETED"
        assert len(completed.clips) >= 1
        # Should have evaluated candidates
        # Should have creative memory populated
        assert len(orchestrator.creative_memory._patterns) > 0
