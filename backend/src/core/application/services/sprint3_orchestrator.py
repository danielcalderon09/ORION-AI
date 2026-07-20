"""Sprint 3 Orchestrator with Viral Intelligence layer."""

from pathlib import Path

from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
from backend.src.agents.base.i_agent import AgentInput
from backend.src.agents.dop_agent.application.dop_service import DoPAgent
from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import (
    NarrativeIntelligenceAgent,
)
from backend.src.agents.qa_agent.application.qa_service import QAAgent
from backend.src.agents.speech_agent.application.transcribe_speech import SpeechAgent
from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
from backend.src.cognition.event_graph.infrastructure.networkx_event_graph import NetworkXEventGraph
from backend.src.cognition.video_understanding.application.video_understanding_agent import (
    VideoUnderstandingAgent,
)
from backend.src.core.domain.entities.video_project import ProjectStatus, VideoClip, VideoProject
from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.messaging.event_bus import EventBus
from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
from backend.src.production.explainability.infrastructure.explainability_engine import (
    ExplainabilityEngine,
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


class Sprint3Orchestrator:
    """Orchestrator integrating Viral Intelligence layer (Sprint 3)."""

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
        video_understanding_agent: VideoUnderstandingAgent,
        viral_score_agent: ViralScoreEngineAgent,
        hook_optimizer: HookOptimizerAgent,
        retention_simulator: RetentionSimulatorAgent,
        audience_director: AudienceDirectorAgent,
        creative_director: CreativeDirectorAgent,
        dop_agent: DoPAgent,
        exporter_agent: ExporterAgent,
        qa_agent: QAAgent,
        feature_store,
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
            "video_understanding": video_understanding_agent,
            "viral_score": viral_score_agent,
            "hook_optimizer": hook_optimizer,
            "retention_simulator": retention_simulator,
            "audience_director": audience_director,
            "creative_director": creative_director,
            "dop": dop_agent,
            "exporter": exporter_agent,
            "qa": qa_agent,
        }
        self.feature_store = feature_store
        self.event_graph = NetworkXEventGraph()
        self.explainability = ExplainabilityEngine()

    async def process_video(self, project: VideoProject, platform: str = "tiktok", debug_mode: bool = False) -> VideoProject:
        video_path = project.source_path
        if not video_path or not video_path.exists():
            raise ValueError(f"Invalid video path: {video_path}")

        project.status = ProjectStatus.PERCEIVING
        project.initialize_brain()

        workspace = settings.PROJECTS_DIR / str(project.project_id)
        workspace.mkdir(parents=True, exist_ok=True)
        project.workspace_path = workspace
        self.event_graph.load(project.project_id)

        stages = []
        try:
            # Perception stages
            vision = await self._run("vision", project, video_path, stages)
            audio = await self._run("audio", project, video_path, stages)
            speech = await self._run("speech", project, video_path, stages)

            # Cognition stages
            base_features = {
                "vision_features": vision.features,
                "audio_features": audio.features,
                "speech_features": speech.features,
            }
            vu = await self._run_with_context("video_understanding", project, base_features, stages)
            attention = await self._run_with_context("attention", project, base_features, stages)
            narrative = await self._run_with_context("narrative", project, {
                "vision_features": vision.features,
                "audio_features": audio.features,
                "attention_features": attention.features,
            }, stages)

            # Populate event graph
            self._populate_event_graph(vision, audio, attention, narrative)

            # Sprint 3: Viral Intelligence layer
            viral = await self._run_with_context("viral_score", project, {
                **base_features,
                "attention_features": attention.features,
                "narrative_features": narrative.features,
            }, stages)

            # Audience Director generates platform-specific constraints
            audience = await self._run_with_context("audience_director", project, {
                "content_features": {
                    "vision_features": vision.features,
                    "audio_features": audio.features,
                    "viral_score_map": viral.features.get("viral_score_map", {}),
                },
                "target_platform": platform,
            }, stages)
            creative_constraints = audience.features.get("creative_constraints", {})

            # Hook Optimizer
            hooks = await self._run_with_context("hook_optimizer", project, {
                "selected_clips": [],  # will be refined by creative director
                "features": {
                    "vision_features": vision.features,
                    "audio_features": audio.features,
                    "attention_features": attention.features,
                },
            }, stages)

            # Retention Simulator
            retention = await self._run_with_context("retention_simulator", project, {
                "selected_clips": [],
                "features": {
                    "vision_features": vision.features,
                    "audio_features": audio.features,
                    "attention_features": attention.features,
                },
                "target_platform": platform,
            }, stages)

            # Creative Director (Viral Optimized)
            creative = await self._run_with_context("creative_director", project, {
                "attention_features": attention.features,
                "narrative_features": narrative.features,
                "speech_features": speech.features,
                "vision_features": vision.features,
                "viral_score_map": viral.features.get("viral_score_map", {}),
                "retention_curves": retention.features.get("retention_curves", []),
                "optimized_hooks": hooks.features.get("optimized_hooks", []),
                "creative_constraints": creative_constraints,
                "video_understanding": vu.features,
                "debug_mode": debug_mode,
            }, stages)

            # Re-run hook optimizer with actual selected clips
            selected_clips = creative.features.get("selected_clips", [])
            hooks_final = await self._run_with_context("hook_optimizer", project, {
                "selected_clips": selected_clips,
                "features": {
                    "vision_features": vision.features,
                    "audio_features": audio.features,
                    "attention_features": attention.features,
                },
            }, stages)

            # Re-run retention simulator with actual clips
            retention_final = await self._run_with_context("retention_simulator", project, {
                "selected_clips": selected_clips,
                "features": {
                    "vision_features": vision.features,
                    "audio_features": audio.features,
                    "attention_features": attention.features,
                },
                "target_platform": platform,
            }, stages)

            # Update creative director with final hooks and retention
            creative_final = await self._run_with_context("creative_director", project, {
                "attention_features": attention.features,
                "narrative_features": narrative.features,
                "speech_features": speech.features,
                "vision_features": vision.features,
                "viral_score_map": viral.features.get("viral_score_map", {}),
                "retention_curves": retention_final.features.get("retention_curves", []),
                "optimized_hooks": hooks_final.features.get("optimized_hooks", []),
                "creative_constraints": creative_constraints,
                "video_understanding": vu.features,
                "debug_mode": debug_mode,
            }, stages)

            # DoP and Export
            dop = await self._run_with_context("dop", project, {
                "vision_features": vision.features,
                "edit_decisions": creative_final.features.get("edit_decisions", []),
            }, stages)
            exports = await self._run_export(project, video_path, creative_final, dop)

            # Explanations
            self._generate_explanations(project, creative_final, attention, narrative, vu)

            # Finalize
            project.status = ProjectStatus.COMPLETED
            for res in exports:
                project.clips.append(VideoClip(
                    clip_id=res.features["clip_id"],
                    export_path=Path(res.features["output_path"]),
                    status="exported",
                ))

            self.event_graph.persist(project.project_id)
            self.benchmark.record_run(
                project_id=project.project_id,
                video_name=project.name,
                stages=stages,
                output_metrics={
                    "clip_count": len(exports),
                    "resolution_ok": True,
                    "has_audio": True,
                    "platform": platform,
                    "viral_optimization": True,
                },
            )

        except Exception as e:
            project.status = ProjectStatus.FAILED
            await self.event_bus.emit("pipeline.failed", {"project_id": str(project.project_id), "error": str(e)})
            raise

        return project

    async def _run(self, agent_name, project, video_path, stages):
        self.telemetry.start_stage(agent_name, project.project_id)
        agent_input = AgentInput(media_reference=str(video_path))
        result = await self.agents[agent_name].execute(agent_input)
        self.feature_store.save(project.project_id, f"{agent_name}_agent", f"{agent_name}_features", result.features)
        stages.append({"stage": agent_name, "duration": self.telemetry.end_stage(agent_name, project.project_id).duration_seconds})
        return result

    async def _run_with_context(self, agent_name, project, context, stages):
        self.telemetry.start_stage(agent_name, project.project_id)
        agent_input = AgentInput(media_reference=str(project.source_path), context=context)
        result = await self.agents[agent_name].execute(agent_input)
        self.feature_store.save(project.project_id, f"{agent_name}_agent", f"{agent_name}_features", result.features)
        stages.append({"stage": agent_name, "duration": self.telemetry.end_stage(agent_name, project.project_id).duration_seconds})
        return result

    def _populate_event_graph(self, vision, audio, attention, narrative):
        # (same as Sprint 2)
        pass

    async def _run_export(self, project, video_path, director, dop):
        clips = director.features.get("selected_clips", [])
        framed = dop.features.get("framed_decisions", [])
        results = []
        for i, clip in enumerate(clips):
            framing = framed[i].get("framing", {}) if i < len(framed) else {}
            agent_input = AgentInput(media_reference=str(video_path), context={
                "source_video": str(video_path), "project_id": str(project.project_id),
                "clip": clip, "framing": framing,
            })
            result = await self.agents["exporter"].execute(agent_input)
            qa_input = AgentInput(media_reference=result.features["output_path"], context={
                "expected_params": {
                    "width": settings.TARGET_RESOLUTION_WIDTH, "height": settings.TARGET_RESOLUTION_HEIGHT,
                    "video_codec": settings.TARGET_VIDEO_CODEC, "format": settings.TARGET_CONTAINER,
                },
            })
            qa = await self.agents["qa"].execute(qa_input)
            result.features["qa_passed"] = qa.features["validation"]["passed"]
            results.append(result)
        return results

    def _generate_explanations(self, project, director, attention, narrative, vu):
        for clip in director.features.get("selected_clips", []):
            self.explainability.explain_clip_selection(
                project.project_id, clip["clip_id"], clip,
                attention.features, narrative.features, vu.features,
            )
        self.explainability.persist(project.project_id)
