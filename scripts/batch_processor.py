#!/usr/bin/env python3
"""Batch video processing utility for CPU/GPU optimization.

Processes multiple videos sequentially or with limited parallelism,
respecting memory budgets and enabling checkpoint resume.

Usage:
    python scripts/batch_processor.py --videos=videos/ --platform=tiktok --profile=balanced --max-workers=2
    python scripts/batch_processor.py --manifest=batch_manifest.json --resume
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.core.domain.entities.video_project import VideoProject, ProjectStatus
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
from backend.src.infrastructure.messaging.event_bus import EventBus
from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore

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

from backend.src.core.application.services.sprint5_orchestrator import Sprint5Orchestrator
from backend.src.infrastructure.profiler.performance_profiler import PerformanceProfiler
from backend.src.infrastructure.memory_manager.memory_manager import MemoryManager
from backend.src.infrastructure.checkpoint.checkpoint_manager import CheckpointManager
from backend.src.infrastructure.pipeline_cache.pipeline_cache import PipelineCache
from backend.src.infrastructure.observability.observability_stack import ObservabilityStack
from backend.src.infrastructure.versioning.versioning_manager import VersioningManager


def build_orchestrator(feature_store) -> Sprint5Orchestrator:
    """Build a fully configured Sprint 5 orchestrator."""
    media = FFmpegMediaAdapter()
    vu_agent = VideoUnderstandingAgent(DummyVideoUnderstandingProvider())
    feedback_collector = FileSystemFeedbackCollector()

    return Sprint5Orchestrator(
        event_bus=EventBus(),
        telemetry=TelemetryService(),
        benchmark=BenchmarkSuite(),
        vision_agent=VisionAgent(media),
        audio_agent=AudioAgent(media),
        speech_agent=SpeechAgent(),
        attention_agent=AttentionAgent(),
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
        reflection_engine=ReflectionEngineAgent(),
        critic_ai=CriticAIAgent(),
        candidate_generator=MultiCandidateGeneratorAgent(num_variants=3),
        consensus_engine=ConsensusEngineAgent(),
        creative_memory=FileSystemCreativeMemory(),
        feedback_collector=feedback_collector,
        feedback_learner=SimpleFeedbackLearner(feedback_collector),
        feature_store=feature_store,
        profiler=PerformanceProfiler(),
        memory_manager=MemoryManager(),
        checkpoint_manager=CheckpointManager(),
        pipeline_cache=PipelineCache(),
        observability=ObservabilityStack(),
        versioning=VersioningManager(),
    )


async def process_single(
    orchestrator: Sprint5Orchestrator,
    video_path: Path,
    platform: str,
    profile: str,
) -> dict[str, Any]:
    """Process a single video and return metrics."""
    project_id = uuid4()
    project = VideoProject(
        project_id=project_id,
        name=video_path.stem,
        source_path=video_path,
    )
    start = time.time()
    try:
        await orchestrator.process_video(project, platform=platform, profile_name=profile)
        status = project.status.value
    except Exception as e:
        status = f"FAILED: {e}"

    duration = time.time() - start
    return {
        "project_id": str(project_id),
        "video": str(video_path),
        "platform": platform,
        "profile": profile,
        "status": status,
        "duration_seconds": round(duration, 2),
        "clips_produced": len(project.clips),
    }


async def process_batch(
    videos: list[Path],
    platform: str,
    profile: str,
    max_workers: int,
    output_manifest: Path,
) -> dict[str, Any]:
    """Process a batch of videos with controlled parallelism."""
    feature_store = FileSystemFeatureStore(settings.ORION_HOME / "features")
    semaphore = asyncio.Semaphore(max_workers)
    results: list[dict[str, Any]] = []

    async def process_with_limit(video: Path) -> dict[str, Any]:
        async with semaphore:
            orchestrator = build_orchestrator(feature_store)
            result = await process_single(orchestrator, video, platform, profile)
            results.append(result)
            # Save incremental manifest
            with open(output_manifest, "w", encoding="utf-8") as f:
                json.dump({"results": results, "in_progress": False}, f, indent=2)
            return result

    tasks = [process_with_limit(v) for v in videos]
    await asyncio.gather(*tasks)

    total_duration = sum(r["duration_seconds"] for r in results)
    success_count = sum(1 for r in results if "FAILED" not in r["status"])
    total_clips = sum(r["clips_produced"] for r in results)

    summary = {
        "total_videos": len(videos),
        "success_count": success_count,
        "failure_count": len(videos) - success_count,
        "total_duration_seconds": round(total_duration, 2),
        "avg_duration_seconds": round(total_duration / len(videos), 2) if videos else 0,
        "total_clips_produced": total_clips,
        "throughput_videos_per_hour": round(len(videos) / (total_duration / 3600), 2) if total_duration > 0 else 0,
    }

    manifest = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "configuration": {
            "platform": platform,
            "profile": profile,
            "max_workers": max_workers,
        },
        "summary": summary,
        "results": results,
    }

    output_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def discover_videos(directory: Path, extensions: tuple[str, ...] = (".mp4", ".mov", ".avi", ".mkv")) -> list[Path]:
    """Discover video files in a directory recursively."""
    return [p for p in directory.rglob("*") if p.suffix.lower() in extensions and p.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Orion AI Batch Processor")
    parser.add_argument("--videos", type=Path, help="Directory containing videos to process")
    parser.add_argument("--manifest", type=Path, help="JSON manifest file with video list")
    parser.add_argument("--platform", default="tiktok", choices=["tiktok", "youtube_shorts", "facebook_reels", "instagram_reels"])
    parser.add_argument("--profile", default="balanced", help="Config profile name")
    parser.add_argument("--max-workers", type=int, default=1, help="Max parallel workers (1 = sequential)")
    parser.add_argument("--output", type=Path, default=Path("batch-manifest.json"), help="Output manifest path")
    parser.add_argument("--resume", action="store_true", help="Resume from existing manifest")
    args = parser.parse_args()

    if args.manifest:
        with open(args.manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
            videos = [Path(p) for p in data.get("videos", [])]
    elif args.videos:
        videos = discover_videos(args.videos)
    else:
        print("Error: Specify --videos directory or --manifest file", file=sys.stderr)
        return 1

    if not videos:
        print("No videos found to process.", file=sys.stderr)
        return 1

    print(f"Batch processing {len(videos)} videos")
    print(f"Platform: {args.platform} | Profile: {args.profile} | Workers: {args.max_workers}")

    # Resume support: skip already processed videos
    if args.resume and args.output.exists():
        with open(args.output, "r", encoding="utf-8") as f:
            existing = json.load(f)
        processed_paths = {r["video"] for r in existing.get("results", [])}
        videos = [v for v in videos if str(v) not in processed_paths]
        print(f"Resumed: {len(videos)} videos remaining after resume")

    manifest = asyncio.run(process_batch(
        videos=videos,
        platform=args.platform,
        profile=args.profile,
        max_workers=args.max_workers,
        output_manifest=args.output,
    ))

    print("\n" + "=" * 60)
    print("BATCH PROCESSING COMPLETE")
    print("=" * 60)
    print(f"Success: {manifest['summary']['success_count']}/{manifest['summary']['total_videos']}")
    print(f"Total clips: {manifest['summary']['total_clips_produced']}")
    print(f"Avg time/video: {manifest['summary']['avg_duration_seconds']:.1f}s")
    print(f"Throughput: {manifest['summary']['throughput_videos_per_hour']:.1f} videos/hour")
    print(f"Manifest: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
