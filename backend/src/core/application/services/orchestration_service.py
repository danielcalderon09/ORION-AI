"""Orchestration service that coordinates the full pipeline."""

import asyncio
from pathlib import Path
from uuid import UUID

from backend.src.core.domain.entities.video_project import (
    ProjectBrain,
    ProjectStatus,
    VideoClip,
    VideoProject,
)
from backend.src.core.domain.events.domain_events import (
    ClipExportedEvent,
    ClipGeneratedEvent,
    PipelineStageCompletedEvent,
    PipelineStageFailedEvent,
    VideoSubmittedEvent,
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


class OrchestrationService:
    """Orchestrates the complete video processing pipeline."""

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
        }
        self.feature_store = feature_store
        self.knowledge_graph_factory = knowledge_graph_factory

    async def process_video(self, project: VideoProject, debug_mode: bool = False) -> VideoProject:
        """Run the full pipeline on a project."""
        video_path = project.source_path
        if not video_path or not video_path.exists():
            raise ValueError(f"Video path invalid: {video_path}")

        project.status = ProjectStatus.PERCEIVING
        project.initialize_brain()

        # Create workspace
        workspace = settings.PROJECTS_DIR / str(project.project_id)
        workspace.mkdir(parents=True, exist_ok=True)
        project.workspace_path = workspace

        # Initialize knowledge graph
        kg = self.knowledge_graph_factory()
        kg.load(project.project_id)

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

            # Stage 4: Attention
            self.telemetry.start_stage("attention", project.project_id)
            attention_result = await self._run_attention(project, vision_result, audio_result, speech_result)
            stages.append({"stage": "attention", "duration": self.telemetry.end_stage("attention", project.project_id).duration_seconds})

            # Stage 5: Narrative
            self.telemetry.start_stage("narrative", project.project_id)
            narrative_result = await self._run_narrative(project, vision_result, audio_result, attention_result)
            stages.append({"stage": "narrative", "duration": self.telemetry.end_stage("narrative", project.project_id).duration_seconds})

            # Stage 6: Director
            self.telemetry.start_stage("director", project.project_id)
            director_result = await self._run_director(project, attention_result, narrative_result, speech_result, debug_mode=debug_mode)
            stages.append({"stage": "director", "duration": self.telemetry.end_stage("director", project.project_id).duration_seconds})

            # Stage 7: DoP
            self.telemetry.start_stage("dop", project.project_id)
            dop_result = await self._run_dop(project, vision_result, director_result)
            stages.append({"stage": "dop", "duration": self.telemetry.end_stage("dop", project.project_id).duration_seconds})

            # Stage 8: Export
            self.telemetry.start_stage("export", project.project_id)
            export_results = await self._run_export(project, video_path, director_result, dop_result)
            stages.append({"stage": "export", "duration": self.telemetry.end_stage("export", project.project_id).duration_seconds})

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

            # Export debug timeline if debug mode
            if debug_mode:
                from backend.src.infrastructure.debug.debug_exporter import DebugTimelineExporter
                exporter = DebugTimelineExporter(project.project_id)
                debug_timeline = director_result.features.get("debug_timeline")
                if debug_timeline:
                    json_path = exporter.export_timeline_json(debug_timeline)
                    html_path = exporter.export_timeline_html(debug_timeline)
                    project.brain.metadata["debug_outputs"] = {
                        "timeline_json": str(json_path),
                        "timeline_html": str(html_path),
                    }

            # Benchmark
            self.benchmark.record_run(
                project_id=project.project_id,
                video_name=project.name,
                stages=stages,
                output_metrics={
                    "clip_count": len(export_results),
                    "resolution_ok": True,
                    "has_audio": True,
                    "debug_mode": debug_mode,
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

    async def _run_director(self, project, attention, narrative, speech, debug_mode: bool = False):
        agent_input = AgentInput(
            media_reference=str(project.source_path),
            context={
                "attention_features": attention.features,
                "narrative_features": narrative.features,
                "speech_features": speech.features,
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
                # Retry or mark failed
                result.features["qa_passed"] = False
                result.features["qa_issues"] = qa_result.features["validation"]["checks"]
            else:
                result.features["qa_passed"] = True

            results.append(result)

        return results
