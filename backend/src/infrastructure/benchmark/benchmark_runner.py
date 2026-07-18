"""Benchmark runner for Sprint 1.5 validation."""

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from backend.src.core.domain.entities.video_project import VideoProject
from backend.src.core.application.services.orchestration_service import OrchestrationService
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite
from backend.src.infrastructure.debug.debug_exporter import DebugTimelineExporter


@dataclass
class BenchmarkVideo:
    """Reference video for benchmarking."""

    name: str
    path: Path
    category: str  # gaming, podcast, tutorial, sports, music, etc.
    expected_duration: float
    description: str


class BenchmarkRunner:
    """Runs the complete pipeline on reference videos and generates reports."""

    def __init__(self, orchestrator: OrchestrationService):
        self.orchestrator = orchestrator
        self.telemetry = TelemetryService()
        self.benchmark = BenchmarkSuite()
        self.results: list[dict] = []

    async def run_single(self, video: BenchmarkVideo, debug: bool = True) -> dict:
        """Process a single reference video."""
        print(f"\n{'='*60}")
        print(f"Benchmarking: {video.name} ({video.category})")
        print(f"{'='*60}")

        project_id = uuid4()
        project = VideoProject(
            project_id=project_id,
            name=video.name,
            source_path=video.path,
        )

        start_time = time.time()
        try:
            completed_project = await self.orchestrator.process_video(project, debug_mode=debug)
            total_time = time.time() - start_time

            # Collect results
            clips = completed_project.clips
            clip_count = len(clips)
            resolutions_ok = all(
                self._check_resolution(c.export_path) for c in clips if c.export_path
            )

            result = {
                "project_id": str(project_id),
                "video_name": video.name,
                "category": video.category,
                "total_time_seconds": round(total_time, 2),
                "clip_count": clip_count,
                "clips": [
                    {
                        "clip_id": str(c.clip_id),
                        "status": c.status,
                        "export_path": str(c.export_path) if c.export_path else None,
                    }
                    for c in clips
                ],
                "resolution_ok": resolutions_ok,
                "telemetry": self.telemetry.get_summary(project_id),
                "status": "success",
            }

            # Export debug timeline if requested
            if debug:
                exporter = DebugTimelineExporter(project_id)
                # Fetch director features from feature store
                try:
                    from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
                    fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
                    director_data = fs.load(project_id, "director_agent", "director_features")
                    debug_timeline = director_data.get("debug_timeline")
                    if debug_timeline:
                        json_path = exporter.export_timeline_json(debug_timeline)
                        html_path = exporter.export_timeline_html(debug_timeline)
                        result["debug_outputs"] = {
                            "json": str(json_path),
                            "html": str(html_path),
                        }
                        print(f"Debug exported: {html_path}")
                except Exception as e:
                    print(f"Debug export failed: {e}")

        except Exception as e:
            total_time = time.time() - start_time
            result = {
                "project_id": str(project_id),
                "video_name": video.name,
                "category": video.category,
                "total_time_seconds": round(total_time, 2),
                "error": str(e),
                "status": "failed",
            }

        self.results.append(result)
        self._print_result(result)
        return result

    async def run_suite(self, videos: list[BenchmarkVideo], debug: bool = True) -> dict:
        """Run full benchmark suite."""
        print(f"\n{'#'*60}")
        print("ORION AI SPRINT 1.5 BENCHMARK SUITE")
        print(f"{'#'*60}")

        for video in videos:
            await self.run_single(video, debug=debug)

        report = self._generate_report()
        self._save_report(report)
        return report

    def _check_resolution(self, clip_path: Path | None) -> bool:
        if not clip_path or not clip_path.exists():
            return False
        try:
            import subprocess
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(clip_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(result.stdout)
            video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
            if video:
                return video.get("width") == settings.TARGET_RESOLUTION_WIDTH and video.get("height") == settings.TARGET_RESOLUTION_HEIGHT
        except Exception:
            pass
        return False

    def _print_result(self, result: dict) -> None:
        print(f"Status: {result['status'].upper()}")
        print(f"Total time: {result.get('total_time_seconds', 0):.1f}s")
        if result.get("clip_count") is not None:
            print(f"Clips generated: {result['clip_count']}")
        if result.get("debug_outputs"):
            print(f"Debug viewer: {result['debug_outputs']['html']}")
        if result.get("error"):
            print(f"ERROR: {result['error']}")
        print("-" * 40)

    def _generate_report(self) -> dict:
        successful = [r for r in self.results if r["status"] == "success"]
        failed = [r for r in self.results if r["status"] == "failed"]

        categories = {}
        for r in successful:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = {"count": 0, "avg_time": 0, "total_clips": 0}
            categories[cat]["count"] += 1
            categories[cat]["avg_time"] += r["total_time_seconds"]
            categories[cat]["total_clips"] += r.get("clip_count", 0)

        for cat in categories:
            if categories[cat]["count"] > 0:
                categories[cat]["avg_time"] /= categories[cat]["count"]
                categories[cat]["avg_clips"] = categories[cat]["total_clips"] / categories[cat]["count"]

        return {
            "suite_name": "Sprint 1.5 Validation",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_videos": len(self.results),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": len(successful) / len(self.results) if self.results else 0,
            "avg_processing_time": sum(r["total_time_seconds"] for r in successful) / len(successful) if successful else 0,
            "category_breakdown": categories,
            "failed_videos": [{"name": r["video_name"], "error": r.get("error", "")} for r in failed],
            "all_results": self.results,
        }

    def _save_report(self, report: dict) -> Path:
        report_dir = settings.ORION_HOME / "benchmarks"
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"sprint_1_5_report_{int(time.time())}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nBenchmark report saved: {path}")
        return path


def create_reference_videos() -> list[BenchmarkVideo]:
    """Create reference video list from a samples directory."""
    samples_dir = settings.ORION_HOME / "benchmark_samples"
    if not samples_dir.exists():
        print(f"WARNING: No benchmark samples found at {samples_dir}")
        print("Create {samples_dir}/ and add test videos.")
        return []

    videos = []
    for video_file in samples_dir.iterdir():
        if video_file.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
            # Infer category from filename prefix
            category = "general"
            name_lower = video_file.stem.lower()
            if "game" in name_lower or "play" in name_lower:
                category = "gaming"
            elif "podcast" in name_lower or "talk" in name_lower:
                category = "podcast"
            elif "sport" in name_lower or "football" in name_lower or "basket" in name_lower:
                category = "sports"
            elif "music" in name_lower or "song" in name_lower:
                category = "music"
            elif "tutorial" in name_lower or "howto" in name_lower:
                category = "tutorial"
            elif "movie" in name_lower or "film" in name_lower or "cinema" in name_lower:
                category = "cinematic"

            videos.append(BenchmarkVideo(
                name=video_file.stem,
                path=video_file,
                category=category,
                expected_duration=0,  # Will be detected
                description=f"Reference {category} video",
            ))

    return videos


async def main():
    """Run benchmark suite."""
    from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
    from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
    from backend.src.infrastructure.cognition.knowledge_graph_impl import InMemoryKnowledgeGraph
    from backend.src.infrastructure.messaging.event_bus import EventBus

    from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
    from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
    from backend.src.agents.speech_agent.application.transcribe_speech import SpeechAgent
    from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
    from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import NarrativeIntelligenceAgent
    from backend.src.agents.director_agent.application.director_service import DirectorAgent
    from backend.src.agents.dop_agent.application.dop_service import DoPAgent
    from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
    from backend.src.agents.qa_agent.application.qa_service import QAAgent

    media = FFmpegMediaAdapter()
    fs = FileSystemFeatureStore(settings.ORION_HOME / "features")
    orchestrator = OrchestrationService(
        event_bus=EventBus(),
        telemetry=TelemetryService(),
        benchmark=BenchmarkSuite(),
        vision_agent=VisionAgent(media),
        audio_agent=AudioAgent(media),
        speech_agent=SpeechAgent(),
        attention_agent=AttentionAgent(),
        narrative_agent=NarrativeIntelligenceAgent(),
        director_agent=DirectorAgent(),
        dop_agent=DoPAgent(),
        exporter_agent=ExporterAgent(media),
        qa_agent=QAAgent(),
        feature_store=fs,
        knowledge_graph_factory=InMemoryKnowledgeGraph,
    )

    runner = BenchmarkRunner(orchestrator)
    videos = create_reference_videos()

    if not videos:
        print("No benchmark videos found. Please add videos to ~/.orion/benchmark_samples/")
        return

    report = await runner.run_suite(videos, debug=True)

    # Print summary
    print(f"\n{'='*60}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*60}")
    print(f"Success rate: {report['success_rate']*100:.1f}%")
    print(f"Avg processing time: {report['avg_processing_time']:.1f}s")
    for cat, stats in report.get("category_breakdown", {}).items():
        print(f"  {cat}: {stats['count']} videos, avg {stats['avg_time']:.1f}s, {stats.get('avg_clips', 0):.1f} clips")
    if report["failed_videos"]:
        print(f"\nFailed videos:")
        for fv in report["failed_videos"]:
            print(f"  - {fv['name']}: {fv['error']}")


if __name__ == "__main__":
    asyncio.run(main())
