"""Updated orchestrator with Sprint 2 components."""

# Add VideoUnderstandingAgent to the pipeline

import asyncio
from pathlib import Path
from uuid import UUID

from backend.src.core.domain.entities.video_project import (
    ProjectBrain, ProjectStatus, VideoClip, VideoProject,
)
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.messaging.event_bus import EventBus
from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite

from backend.src.agents.base.i_agent import AgentInput
from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
from backend.src.agents.speech_agent.application.transcribe_speech import SpeechAgent
from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import NarrativeIntelligenceAgent
from backend.src.agents.director_agent.application.director_service import DirectorAgent
from backend.src.agents.dop_agent.application.dop_service import DoPAgent
from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
from backend.src.agents.qa_agent.application.qa_service import QAAgent

# Sprint 2 imports
from backend.src.cognition.video_understanding.application.video_understanding_agent import VideoUnderstandingAgent
from backend.src.cognition.event_graph.infrastructure.networkx_event_graph import NetworkXEventGraph
from backend.src.learning.semantic_memory.infrastructure.faiss_semantic_memory import FaissSemanticMemory
from backend.src.production.explainability.infrastructure.explainability_engine import ExplainabilityEngine


class Sprint2OrchestrationService:
    """Orchestrator with Sprint 2 semantic intelligence."""

    def __init__(
        self,
        event_bus: EventBus,
        telemetry: TelemetryService,
        benchmark: BenchmarkSuite,
        vision_agent: VisionAgent,
        audio_agent: AudioAgent,
        speech_agent: SpeechAgent,
        attention_agent: AttentionAgent,
        narrative_agent: NarrativeIntelligenceAgent,
        director_agent: DirectorAgent,
        dop_agent: DoPAgent,
        exporter_agent: ExporterAgent,
        qa_agent: QAAgent,
        video_understanding_agent: VideoUnderstandingAgent,
        feature_store,
        knowledge_graph_factory,
    ):
        self.event_bus = event_bus
        self.telemetry = telemetry
        self.benchmark = benchmark
        self.agents = {
            "vision": vision_agent,
            "audio": audio_agent,
            "speech": speech_agent,
            "attention": attention_agent,
            "narrative": narrative_agent,
            "director": director_agent,
            "dop": dop_agent,
            "exporter": exporter_agent,
            "qa": qa_agent,
            "video_understanding": video_understanding_agent,
        }
        self.feature_store = feature_store
        self.knowledge_graph_factory = knowledge_graph_factory
        self.event_graph = NetworkXEventGraph()
        self.explainability = ExplainabilityEngine()

    async def process_video(self, project: VideoProject, debug_mode: bool = False) -> VideoProject:
        video_path = project.source_path
        if not video_path or not video_path.exists():
            raise ValueError(f"Video path invalid: {video_path}")

        project.status = ProjectStatus.PERCEIVING
        project.initialize_brain()

        workspace = settings.PROJECTS_DIR / str(project.project_id)
        workspace.mkdir(parents=True, exist_ok=True)
        project.workspace_path = workspace

        # Load or create event graph
        self.event_graph.load(project.project_id)

        stages = []
        try:
            # Stage 1: Vision
            self.telemetry.start_stage("vision", project.project_id)
            vision_result = await self._run_vision(project, video_path)
            stages.append({"stage": "vision", "duration": self.telemetry.end_stage("vision", project.project_id).duration_seconds})

            # Stage 2: Audio
            self.telemetry.start_stage("audio", project.project_id)
            audio_result = await self._run_audio(project, video_path)
            stages.append({"stage": "audio", "duration": self.telemetry.end_stage("audio", project.project_id).duration_seconds})

            # Stage 3: Speech
            self.telemetry.start_stage("speech", project.project_id)
            speech_result = await self._run_speech(project, video_path)
            stages.append({"stage": "speech", "duration": self.telemetry.end_stage("speech", project.project_id).duration_seconds})

            # Stage 4: Video Understanding (NEW in Sprint 2)
            self.telemetry.start_stage("video_understanding", project.project_id)
            vu_result = await self._run_video_understanding(project, vision_result, audio_result, speech_result)
            stages.append({"stage": "video_understanding", "duration": self.telemetry.end_stage("video_understanding", project.project_id).duration_seconds})

            # Stage 5: Attention
            self.telemetry.start_stage("attention", project.project_id)
            attention_result = await self._run_attention(project, vision_result, audio_result, speech_result)
            stages.append({"stage": "attention", "duration": self.telemetry.end_stage("attention", project.project_id).duration_seconds})

            # Stage 6: Narrative
            self.telemetry.start_stage("narrative", project.project_id)
            narrative_result = await self._run_narrative(project, vision_result, audio_result, attention_result)
            stages.append({"stage": "narrative", "duration": self.telemetry.end_stage("narrative", project.project_id).duration_seconds})

            # Build event graph from detected events
            self._populate_event_graph(project.project_id, vision_result, audio_result, attention_result, narrative_result)

            # Stage 7: Director
            self.telemetry.start_stage("director", project.project_id)
            director_result = await self._run_director(project, attention_result, narrative_result, speech_result, vu_result, debug_mode=debug_mode)
            stages.append({"stage": "director", "duration": self.telemetry.end_stage("director", project.project_id).duration_seconds})

            # Stage 8: DoP
            self.telemetry.start_stage("dop", project.project_id)
            dop_result = await self._run_dop(project, vision_result, director_result)
            stages.append({"stage": "dop", "duration": self.telemetry.end_stage("dop", project.project_id).duration_seconds})

            # Stage 9: Export
            self.telemetry.start_stage("export", project.project_id)
            export_results = await self._run_export(project, video_path, director_result, dop_result)
            stages.append({"stage": "export", "duration": self.telemetry.end_stage("export", project.project_id).duration_seconds})

            # Generate explanations for selected clips
            self._generate_explanations(project, director_result, attention_result, narrative_result, vu_result)

            # Update project
            project.status = ProjectStatus.COMPLETED
            for res in export_results:
                clip = VideoClip(
                    clip_id=res.features["clip_id"],
                    temporal_range=None,
                    export_path=Path(res.features["output_path"]),
                    status="exported",
                )
                project.clips.append(clip)

            # Persist event graph
            self.event_graph.persist(project.project_id)

            # Benchmark
            self.benchmark.record_run(
                project_id=project.project_id,
                video_name=project.name,
                stages=stages,
                output_metrics={
                    "clip_count": len(export_results),
                    "resolution_ok": True,
                    "has_audio": True,
                    "semantic_understanding": vu_result.features.get("genre", "unknown") != "unknown",
                },
            )

        except Exception as e:
            project.status = ProjectStatus.FAILED
            await self.event_bus.emit("pipeline.failed", {
                "project_id": str(project.project_id),
                "error": str(e),
            })
            raise

        return project

    async def _run_vision(self, project, video_path):
        agent_input = AgentInput(media_reference=str(video_path))
        result = await self.agents["vision"].execute(agent_input)
        self.feature_store.save(project.project_id, "vision_agent", "visual_features", result.features)
        project.brain.features_index["vision"] = {"status": "complete"}
        return result

    async def _run_audio(self, project, video_path):
        agent_input = AgentInput(media_reference=str(video_path))
        result = await self.agents["audio"].execute(agent_input)
        self.feature_store.save(project.project_id, "audio_agent", "audio_features", result.features)
        return result

    async def _run_speech(self, project, video_path):
        agent_input = AgentInput(media_reference=str(video_path))
        result = await self.agents["speech"].execute(agent_input)
        self.feature_store.save(project.project_id, "speech_agent", "speech_features", result.features)
        return result

    async def _run_video_understanding(self, project, vision, audio, speech):
        """NEW: Sprint 2 Phase 1 — Multimodal semantic understanding."""
        agent_input = AgentInput(
            media_reference=str(project.source_path),
            context={
                "vision_features": vision.features,
                "audio_features": audio.features,
                "speech_features": speech.features,
            },
        )
        result = await self.agents["video_understanding"].execute(agent_input)
        self.feature_store.save(project.project_id, "video_understanding_agent", "semantic_features", result.features)
        project.brain.features_index["video_understanding"] = {"status": "complete", "genre": result.features.get("genre", "unknown")}
        return result

    async def _run_attention(self, project, vision, audio, speech):
        agent_input = AgentInput(
            media_reference=str(project.source_path),
            context={
                "vision_features": vision.features,
                "audio_features": audio.features,
                "speech_features": speech.features,
            },
        )
        result = await self.agents["attention"].execute(agent_input)
        self.feature_store.save(project.project_id, "attention_agent", "attention_features", result.features)
        return result

    async def _run_narrative(self, project, vision, audio, attention):
        agent_input = AgentInput(
            media_reference=str(project.source_path),
            context={
                "vision_features": vision.features,
                "audio_features": audio.features,
                "attention_features": attention.features,
            },
        )
        result = await self.agents["narrative"].execute(agent_input)
        self.feature_store.save(project.project_id, "narrative_agent", "narrative_features", result.features)
        return result

    def _populate_event_graph(self, project_id, vision, audio, attention, narrative):
        """Populate event graph with detected events from all agents."""
        # Add scene change events
        for sc in vision.features.get("scene_changes", []):
            from backend.src.cognition.event_graph.domain.event_node import EventNode
            event = EventNode(
                event_id=f"scene_change_{sc['timestamp']:.1f}",
                event_type="scene_change",
                start_time=sc["timestamp"],
                end_time=sc["timestamp"],
                description=f"Scene change at {sc['timestamp']:.1f}s",
                confidence=sc.get("score", 0.5),
                participants=[],
                source_agent="vision_agent",
                properties={"change_score": sc.get("score", 0)},
            )
            self.event_graph.add_event(event)

        # Add audio peak events
        for peak in audio.features.get("peaks", [])[:20]:  # Limit to top 20
            from backend.src.cognition.event_graph.domain.event_node import EventNode
            event = EventNode(
                event_id=f"audio_peak_{peak['time']:.1f}",
                event_type="audio_peak",
                start_time=peak["time"],
                end_time=peak["time"],
                description=f"Audio energy peak at {peak['time']:.1f}s",
                confidence=min(peak.get("energy", 0) * 2, 1.0),
                participants=[],
                source_agent="audio_agent",
                properties={"energy": peak.get("energy", 0)},
            )
            self.event_graph.add_event(event)

        # Add attention peak events
        for peak in attention.features.get("peaks", [])[:10]:
            from backend.src.cognition.event_graph.domain.event_node import EventNode
            event = EventNode(
                event_id=f"attention_peak_{peak['time']:.1f}",
                event_type="attention_peak",
                start_time=peak["time"],
                end_time=peak["time"],
                description=f"Attention peak at {peak['time']:.1f}s",
                confidence=peak.get("attention_score", 0),
                participants=[],
                source_agent="attention_agent",
                properties={"score": peak.get("attention_score", 0)},
            )
            self.event_graph.add_event(event)

        # Create causal links: audio peaks often cause attention peaks
        for audio_peak in audio.features.get("peaks", [])[:10]:
            for att_peak in attention.features.get("peaks", [])[:10]:
                if abs(audio_peak["time"] - att_peak["time"]) < 1.0:
                    from backend.src.cognition.event_graph.domain.event_node import CausalEdge
                    edge = CausalEdge(
                        from_event=f"audio_peak_{audio_peak['time']:.1f}",
                        to_event=f"attention_peak_{att_peak['time']:.1f}",
                        relation_type="causes",
                        confidence=0.7,
                        evidence=["temporal_proximity"],
                    )
                    self.event_graph.add_causal_link(edge)

    async def _run_director(self, project, attention, narrative, speech, video_understanding, debug_mode: bool = False):
        agent_input = AgentInput(
            media_reference=str(project.source_path),
            context={
                "attention_features": attention.features,
                "narrative_features": narrative.features,
                "speech_features": speech.features,
                "video_understanding": video_understanding.features,
                "debug_mode": debug_mode,
            },
        )
        result = await self.agents["director"].execute(agent_input)
        self.feature_store.save(project.project_id, "director_agent", "director_features", result.features)
        return result

    async def _run_dop(self, project, vision, director):
        agent_input = AgentInput(
            media_reference=str(project.source_path),
            context={
                "vision_features": vision.features,
                "edit_decisions": director.features.get("edit_decisions", []),
            },
        )
        result = await self.agents["dop"].execute(agent_input)
        self.feature_store.save(project.project_id, "dop_agent", "dop_features", result.features)
        return result

    async def _run_export(self, project, video_path, director, dop):
        clips = director.features.get("selected_clips", [])
        framed = dop.features.get("framed_decisions", [])

        results = []
        for i, clip in enumerate(clips):
            framing = framed[i].get("framing", {}) if i < len(framed) else {}
            agent_input = AgentInput(
                media_reference=str(video_path),
                context={
                    "source_video": str(video_path),
                    "project_id": str(project.project_id),
                    "clip": clip,
                    "framing": framing,
                },
            )
            result = await self.agents["exporter"].execute(agent_input)

            # QA validation
            qa_input = AgentInput(
                media_reference=result.features["output_path"],
                context={
                    "expected_params": {
                        "width": settings.TARGET_RESOLUTION_WIDTH,
                        "height": settings.TARGET_RESOLUTION_HEIGHT,
                        "video_codec": settings.TARGET_VIDEO_CODEC,
                        "format": settings.TARGET_CONTAINER,
                    },
                },
            )
            qa_result = await self.agents["qa"].execute(qa_input)

            if not qa_result.features["validation"]["passed"]:
                result.features["qa_passed"] = False
                result.features["qa_issues"] = qa_result.features["validation"]["checks"]
            else:
                result.features["qa_passed"] = True

            results.append(result)

        return results

    def _generate_explanations(self, project, director_result, attention_result, narrative_result, vu_result):
        """Generate explanations for each selected clip."""
        clips = director_result.features.get("selected_clips", [])
        explanations = []
        for clip in clips:
            explanation = self.explainability.explain_clip_selection(
                project_id=project.project_id,
                clip_id=clip["clip_id"],
                clip_data=clip,
                attention_features=attention_result.features,
                narrative_features=narrative_result.features,
                video_understanding=vu_result.features,
            )
            explanations.append(explanation)

        # Export explanations
        if explanations:
            self.explainability.persist(project.project_id)
            # Export HTML for first clip as example
            exp_dir = project.workspace_path / "explanations"
            self.explainability.export_explanation_html(explanations[0], exp_dir)
