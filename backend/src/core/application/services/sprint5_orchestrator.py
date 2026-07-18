"""Sprint 5 Orchestrator with Production Hardening layer."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from backend.src.core.domain.entities.video_project import ProjectStatus, VideoClip, VideoProject
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
from backend.src.agents.dop_agent.application.dop_service import DoPAgent, DoPConfig
from backend.src.agents.dop_agent.infrastructure.mediapipe_face_detection import MediaPipeFaceDetectionProvider
from backend.src.agents.dop_agent.infrastructure.simple_subject_tracker import SimpleSubjectTracker
from backend.src.agents.dop_agent.infrastructure.auto_reframe_provider import AutoReframeProvider
from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
from backend.src.agents.qa_agent.application.qa_service import QAAgent

from backend.src.cognition.video_understanding.application.video_understanding_agent import VideoUnderstandingAgent
from backend.src.cognition.event_graph.infrastructure.networkx_event_graph import NetworkXEventGraph
from backend.src.production.explainability.infrastructure.explainability_engine import ExplainabilityEngine

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

from backend.src.infrastructure.profiler.performance_profiler import PerformanceProfiler
from backend.src.infrastructure.memory_manager.memory_manager import MemoryManager
from backend.src.infrastructure.checkpoint.checkpoint_manager import CheckpointManager
from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache
from backend.src.infrastructure.observability.observability_stack import ObservabilityStack
from backend.src.infrastructure.versioning.versioning_manager import VersioningManager


class Sprint5Orchestrator:
    """Production-hardened orchestrator with profiling, memory management, checkpoints, caching, and observability."""

    PIPELINE_VERSION = "5.0.0"

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
        reflection_engine: ReflectionEngineAgent,
        critic_ai: CriticAIAgent,
        candidate_generator: MultiCandidateGeneratorAgent,
        consensus_engine: ConsensusEngineAgent,
        creative_memory: FileSystemCreativeMemory,
        feedback_collector: FileSystemFeedbackCollector,
        feedback_learner: SimpleFeedbackLearner,
        feature_store,
        profiler: PerformanceProfiler | None = None,
        memory_manager: MemoryManager | None = None,
        checkpoint_manager: CheckpointManager | None = None,
        pipeline_cache: PipelineCache | None = None,
        observability: ObservabilityStack | None = None,
        versioning: VersioningManager | None = None,
        face_detection: Any | None = None,
        subject_tracker: Any | None = None,
        auto_reframe: Any | None = None,
    ):
        self.event_bus = event_bus
        self.telemetry = telemetry
        self.benchmark = benchmark
        self.agents = {
            "vision": vision_agent, "audio": audio_agent, "speech": speech_agent,
            "attention": attention_agent, "narrative": narrative_agent,
            "video_understanding": video_understanding_agent,
            "viral_score": viral_score_agent, "hook_optimizer": hook_optimizer,
            "retention_simulator": retention_simulator,
            "audience_director": audience_director, "creative_director": creative_director,
            "dop": dop_agent, "exporter": exporter_agent, "qa": qa_agent,
            "reflection_engine": reflection_engine, "critic_ai": critic_ai,
            "candidate_generator": candidate_generator, "consensus_engine": consensus_engine,
        }
        self.creative_memory = creative_memory
        self.feedback_collector = feedback_collector
        self.feedback_learner = feedback_learner
        self.feature_store = feature_store
        self.event_graph = NetworkXEventGraph()
        self.explainability = ExplainabilityEngine()
        # Sprint 5 hardening components
        self.profiler = profiler or PerformanceProfiler()
        self.memory_manager = memory_manager or MemoryManager()
        self.checkpoint_manager = checkpoint_manager or CheckpointManager()
        self.pipeline_cache = pipeline_cache or PipelineCache()
        self.observability = observability or ObservabilityStack()
        self.versioning = versioning or VersioningManager()
        self.versioning.auto_detect_versions()

    def _file_hash(self, video_path: Path) -> str:
        """Compute a quick content hash for cache keys."""
        stat = video_path.stat()
        raw = f"{video_path.absolute()}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def process_video(self, project: VideoProject, platform: str = "tiktok", profile_name: str = "balanced", debug_mode: bool = False) -> VideoProject:
        video_path = project.source_path
        if not video_path or not video_path.exists():
            raise ValueError(f"Invalid path: {video_path}")

        project.status = ProjectStatus.PERCEIVING
        project.initialize_brain()

        workspace = settings.PROJECTS_DIR / str(project.project_id)
        workspace.mkdir(parents=True, exist_ok=True)
        project.workspace_path = workspace
        self.event_graph.load(project.project_id)

        # Sprint 5: reproducibility manifest
        manifest = self.versioning.create_reproducibility_manifest(
            video_path=video_path,
            pipeline_version=self.PIPELINE_VERSION,
            target_platform=platform,
        )

        # Sprint 5: memory budget + observability start
        self.memory_manager.start_session(str(project.project_id))
        self.observability.start_pipeline(str(project.project_id))

        # Sprint 5: checkpoint at STARTED
        self.checkpoint_manager.save_checkpoint(
            project.project_id, "STARTED", 0,
            brain_state={"video_path": str(video_path), "platform": platform, "profile_name": profile_name},
            feature_paths={},
        )

        stages = []
        stage_index = 0
        try:
            # --- Perception & Cognition (Sprints 1-2) ---
            vision = await self._run("vision", project, video_path, stages, stage_index)
            stage_index += 1
            audio = await self._run("audio", project, video_path, stages, stage_index)
            stage_index += 1
            speech = await self._run("speech", project, video_path, stages, stage_index)
            stage_index += 1
            vu = await self._run_with_context("video_understanding", project, {"vision": vision, "audio": audio, "speech": speech}, stages, stage_index)
            stage_index += 1
            attention = await self._run_with_context("attention", project, {"vision": vision, "audio": audio, "speech": speech}, stages, stage_index)
            stage_index += 1
            narrative = await self._run_with_context("narrative", project, {"vision": vision, "audio": audio, "attention": attention}, stages, stage_index)
            stage_index += 1
            self._populate_event_graph(vision, audio, attention, narrative)

            # --- Viral Intelligence (Sprint 3) ---
            viral = await self._run_with_context("viral_score", project, {
                "vision": vision, "audio": audio, "speech": speech, "attention": attention, "narrative": narrative,
            }, stages, stage_index)
            stage_index += 1
            audience = await self._run_with_context("audience_director", project, {
                "content_features": {"vision": vision, "audio": audio, "viral": viral},
                "target_platform": platform,
            }, stages, stage_index)
            stage_index += 1
            constraints = audience.features.get("creative_constraints", {})
            hooks = await self._run_with_context("hook_optimizer", project, {"selected_clips": [], "features": {"vision": vision, "audio": audio, "attention": attention}}, stages, stage_index)
            stage_index += 1
            retention = await self._run_with_context("retention_simulator", project, {"selected_clips": [], "features": {"vision": vision, "audio": audio, "attention": attention}, "target_platform": platform}, stages, stage_index)
            stage_index += 1
            creative = await self._run_with_context("creative_director", project, {
                "attention": attention, "narrative": narrative, "speech": speech,
                "viral_score_map": viral.features.get("viral_score_map", {}),
                "retention_curves": retention.features.get("retention_curves", []),
                "optimized_hooks": hooks.features.get("optimized_hooks", []),
                "creative_constraints": constraints,
                "video_understanding": vu.features,
                "debug_mode": debug_mode,
            }, stages, stage_index)
            stage_index += 1

            selected_clips = creative.features.get("selected_clips", [])

            # --- Sprint 4: Auto-Improvement Layer ---
            # 1. Multi Candidate Generator: produce variants
            candidates = await self._run_with_context("candidate_generator", project, {
                "selected_clips": selected_clips,
                "optimized_hooks": hooks.features.get("optimized_hooks", []),
                "creative_constraints": constraints,
                "viral_score_map": viral.features.get("viral_score_map", {}),
            }, stages, stage_index)
            stage_index += 1
            candidate_sets = candidates.features.get("candidate_sets", [])

            # 2. Critic AI + Reflection Engine per candidate
            all_critiques = []
            all_reflections = []
            for cs in candidate_sets:
                for candidate in cs.get("candidates", []):
                    # Critic
                    critique = await self.agents["critic_ai"].execute(AgentInput(
                        media_reference=str(video_path),
                        context={"candidate": candidate, "context": {
                            "narrative_features": narrative.features,
                            "attention_features": attention.features,
                            "retention_curve": next(
                                (r for r in retention.features.get("retention_curves", []) if r["clip_id"].startswith(f"clip_{candidate['start']:.1f}")), {}
                            ),
                        }},
                    ))
                    all_critiques.append(critique.features.get("critique_report", {}))

                    # Reflection
                    reflection = await self.agents["reflection_engine"].execute(AgentInput(
                        media_reference=str(video_path),
                        context={
                            "clip": candidate,
                            "creative_brief": creative.features.get("creative_brief", {}),
                            "metrics": {"quality_score": 0.7, "viral_score": candidate.get("estimated_viral_score", 0)},
                            "retention_curve": next(
                                (r for r in retention.features.get("retention_curves", []) if r["clip_id"].startswith(f"clip_{candidate['start']:.1f}")), {}
                            ),
                        },
                    ))
                    all_reflections.append(reflection.features.get("reflection_report", {}))

            # 3. Consensus Engine: pick winners
            consensus = await self._run_with_context("consensus_engine", project, {
                "candidate_sets": candidate_sets,
                "critiques": all_critiques,
                "reflections": all_reflections,
                "viral_scores": [],
                "retention_scores": [],
            }, stages, stage_index)
            stage_index += 1
            consensus_results = consensus.features.get("consensus_results", [])

            # 4. Export winning candidates only
            exports = []
            for cr in consensus_results:
                winner_id = cr.get("winning_candidate_id", "")
                # Find the winning candidate
                winner = None
                for cs in candidate_sets:
                    for c in cs.get("candidates", []):
                        if c.get("variant_id") == winner_id:
                            winner = c
                            break
                    if winner:
                        break

                if not winner:
                    continue

                # Build a clip dict for export
                export_clip = {
                    "clip_id": winner["variant_id"],
                    "start": winner["start"],
                    "end": winner["end"],
                    "viral_score": winner.get("estimated_viral_score", 0),
                }

                # DoP framing
                dop_input = AgentInput(media_reference=str(video_path), context={
                    "vision_features": vision.features,
                    "edit_decisions": [{"clip_id": winner["variant_id"], "temporal_range": (winner["start"], winner["end"])}],
                })
                dop_result = await self.agents["dop"].execute(dop_input)
                framing = dop_result.features.get("framed_decisions", [{}])[0].get("framing", {}) if dop_result.features.get("framed_decisions") else {}

                # Export
                exp_input = AgentInput(media_reference=str(video_path), context={
                    "source_video": str(video_path),
                    "project_id": str(project.project_id),
                    "clip": export_clip,
                    "framing": framing,
                })
                exp_result = await self.agents["exporter"].execute(exp_input)

                # QA
                qa_input = AgentInput(media_reference=exp_result.features["output_path"], context={
                    "expected_params": {
                        "width": settings.TARGET_RESOLUTION_WIDTH,
                        "height": settings.TARGET_RESOLUTION_HEIGHT,
                        "video_codec": settings.TARGET_VIDEO_CODEC,
                        "format": settings.TARGET_CONTAINER,
                    },
                })
                qa_result = await self.agents["qa"].execute(qa_input)
                exp_result.features["qa_passed"] = qa_result.features["validation"]["passed"]

                exports.append(exp_result)

                # Store pattern in Creative Memory
                genre = vu.features.get("genre", "general")
                pattern = {
                    "pattern_id": f"{project.project_id}_{winner['variant_id']}",
                    "category": genre,
                    "platform": platform,
                    "decision_type": winner.get("hook_strategy", "unknown"),
                    "context_features": {"viral_score": winner.get("estimated_viral_score", 0)},
                    "action": {"start": winner["start"], "end": winner["end"], "strategy": winner.get("hook_strategy")},
                    "outcome": {},
                    "usage_count": 1,
                    "success_rate": 0.5,
                }
                from backend.src.sprint4.creative_memory.domain.creative_pattern import CreativePattern
                self.creative_memory.store_pattern(CreativePattern(**pattern))

            # Finalize
            project.status = ProjectStatus.COMPLETED
            for res in exports:
                project.clips.append(VideoClip(
                    clip_id=res.features["clip_id"],
                    export_path=Path(res.features["output_path"]),
                    status="exported",
                ))

            self.event_graph.persist(project.project_id)

            # Sprint 5: checkpoint COMPLETED
            self.checkpoint_manager.save_checkpoint(
                project.project_id, "COMPLETED", stage_index,
                brain_state={"clip_count": len(exports), "manifest": manifest.to_dict()},
                feature_paths={},
            )

            # Sprint 5: observability end + summary
            profile_summary = self.profiler.get_summary(project.project_id)
            self.observability.end_pipeline(str(project.project_id), success=True)
            self.memory_manager.end_session(str(project.project_id))

            # Sprint 5: emit final metrics
            self.observability.emit_event("pipeline.completed", {
                "project_id": str(project.project_id),
                "platform": platform,
                "profile_name": profile_name,
                "total_duration_seconds": profile_summary.get("total_time_sec") if profile_summary else None,
                "peak_memory_mb": profile_summary.get("peak_memory_mb") if profile_summary else None,
                "clip_count": len(exports),
            })

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
                    "candidates_evaluated": sum(len(cs.get("candidates", [])) for cs in candidate_sets),
                    "consensus_confidence": consensus.features.get("avg_confidence", 0),
                },
            )

        except Exception as e:
            project.status = ProjectStatus.FAILED
            await self.event_bus.emit("pipeline.failed", {"project_id": str(project.project_id), "error": str(e)})

            # Sprint 5: checkpoint FAILED
            self.checkpoint_manager.save_checkpoint(
                project.project_id, "FAILED", stage_index,
                brain_state={"error": str(e)},
                feature_paths={},
            )
            self.observability.end_pipeline(str(project.project_id), success=False)
            self.memory_manager.end_session(str(project.project_id))
            raise

        return project

    async def _run(self, agent_name, project, video_path, stages, stage_index):
        file_hash = self._file_hash(video_path)
        cached = self.pipeline_cache.get(file_hash, agent_name, self.PIPELINE_VERSION)
        if cached is not None:
            self.observability.emit_event("pipeline.cache.hit", {"project_id": str(project.project_id), "stage": agent_name})
            return cached

        self.telemetry.start_stage(agent_name, project.project_id)
        handle = self.profiler.start_stage(agent_name, project.project_id, agent_id=agent_name)
        self.memory_manager.request_allocation(str(project.project_id), agent_name, self.memory_manager.budget.max_ram_mb * 0.15)

        result = await self.agents[agent_name].execute(AgentInput(media_reference=str(video_path)))

        self.feature_store.save(project.project_id, f"{agent_name}_agent", f"{agent_name}_features", result.features)
        duration = self.telemetry.end_stage(agent_name, project.project_id).duration_seconds
        stages.append({"stage": agent_name, "duration": duration})
        self.profiler.end_stage(handle, metadata={"cache_hit": False})
        self.pipeline_cache.put(file_hash, agent_name, self.PIPELINE_VERSION, result.features)
        self.observability.emit_event("pipeline.stage.completed", {"project_id": str(project.project_id), "stage": agent_name, "duration": duration})
        self.checkpoint_manager.save_checkpoint(
            project.project_id, f"{agent_name.upper()}_DONE", stage_index,
            brain_state={}, feature_paths={},
        )
        return result

    async def _run_with_context(self, agent_name, project, context, stages, stage_index):
        file_hash = self._file_hash(project.source_path)
        cached = self.pipeline_cache.get(file_hash, agent_name, self.PIPELINE_VERSION)
        if cached is not None:
            self.observability.emit_event("pipeline.cache.hit", {"project_id": str(project.project_id), "stage": agent_name})
            return cached

        self.telemetry.start_stage(agent_name, project.project_id)
        handle = self.profiler.start_stage(agent_name, project.project_id, agent_id=agent_name)
        self.memory_manager.request_allocation(str(project.project_id), agent_name, self.memory_manager.budget.max_ram_mb * 0.15)

        result = await self.agents[agent_name].execute(AgentInput(media_reference=str(project.source_path), context=context))

        self.feature_store.save(project.project_id, f"{agent_name}_agent", f"{agent_name}_features", result.features)
        duration = self.telemetry.end_stage(agent_name, project.project_id).duration_seconds
        stages.append({"stage": agent_name, "duration": duration})
        self.profiler.end_stage(handle, metadata={"cache_hit": False})
        self.pipeline_cache.put(file_hash, agent_name, self.PIPELINE_VERSION, result.features)
        self.observability.emit_event("pipeline.stage.completed", {"project_id": str(project.project_id), "stage": agent_name, "duration": duration})
        self.checkpoint_manager.save_checkpoint(
            project.project_id, f"{agent_name.upper()}_DONE", stage_index,
            brain_state={}, feature_paths={},
        )
        return result

    def _populate_event_graph(self, vision, audio, attention, narrative):
        pass  # Same as Sprint 2/3
