"""Benchmark suite for pipeline quality measurement."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from backend.src.infrastructure.config.settings import settings


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""

    run_id: str
    video_name: str
    project_id: str
    total_duration_seconds: float
    pipeline_stages: list[dict[str, Any]]
    output_metrics: dict[str, Any] = field(default_factory=dict)
    passed: bool = True


class BenchmarkSuite:
    """Runs benchmarks against reference videos and stores results."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.results: list[BenchmarkResult] = []
        self.reference_dir = settings.ORION_HOME / "benchmarks"
        self.reference_dir.mkdir(parents=True, exist_ok=True)

    def record_run(
        self,
        project_id: UUID,
        video_name: str,
        stages: list[dict[str, Any]],
        output_metrics: dict[str, Any],
    ) -> BenchmarkResult:
        """Record a benchmark run."""
        if not self.enabled:
            return None

        total_time = sum(s.get("duration", 0) for s in stages)
        result = BenchmarkResult(
            run_id=f"{project_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            video_name=video_name,
            project_id=str(project_id),
            total_duration_seconds=total_time,
            pipeline_stages=stages,
            output_metrics=output_metrics,
        )
        self.results.append(result)
        self._save_result(result)
        return result

    def _save_result(self, result: BenchmarkResult) -> None:
        path = self.reference_dir / f"{result.run_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.__dict__, f, indent=2, default=str)

    def compare_to_baseline(self, run_id: str) -> dict[str, Any]:
        """Compare a run to baseline expectations."""
        # Sprint 1: Basic threshold checks
        result = next((r for r in self.results if r.run_id == run_id), None)
        if not result:
            return {"error": "Run not found"}

        checks = {
            "total_time_under_5min": result.total_duration_seconds < 300,
            "at_least_one_clip": result.output_metrics.get("clip_count", 0) >= 1,
            "correct_resolution": result.output_metrics.get("resolution_ok", False),
            "has_audio": result.output_metrics.get("has_audio", False),
        }
        all_passed = all(checks.values())
        return {
            "run_id": run_id,
            "passed": all_passed,
            "checks": checks,
        }

    def get_baseline_summary(self) -> dict[str, Any]:
        """Get summary of all benchmark runs."""
        if not self.results:
            return {"runs": 0}
        return {
            "runs": len(self.results),
            "avg_total_time": sum(r.total_duration_seconds for r in self.results) / len(self.results),
            "pass_rate": sum(1 for r in self.results if r.passed) / len(self.results),
        }
