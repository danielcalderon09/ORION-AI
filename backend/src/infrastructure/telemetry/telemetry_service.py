"""Local telemetry for performance and resource monitoring."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psutil

from backend.src.infrastructure.config.settings import settings


@dataclass
class TelemetryRecord:
    """Single telemetry measurement."""

    timestamp: datetime
    project_id: str | None
    stage: str
    duration_seconds: float
    cpu_percent: float
    memory_mb: float
    gpu_memory_mb: float | None
    extra: dict[str, Any] = field(default_factory=dict)


class TelemetryService:
    """Records performance telemetry locally."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.records: list[TelemetryRecord] = []
        self._start_times: dict[str, float] = {}

    def start_stage(self, stage_name: str, project_id: UUID | None = None) -> None:
        """Mark the start of a processing stage."""
        if not self.enabled:
            return
        key = f"{project_id}:{stage_name}" if project_id else stage_name
        self._start_times[key] = time.time()

    def end_stage(self, stage_name: str, project_id: UUID | None = None, extra: dict | None = None) -> TelemetryRecord | None:
        """Mark the end of a processing stage and record metrics."""
        if not self.enabled:
            return None
        key = f"{project_id}:{stage_name}" if project_id else stage_name
        start = self._start_times.pop(key, None)
        if start is None:
            return None

        duration = time.time() - start
        mem = psutil.Process().memory_info().rss / (1024 * 1024)
        cpu = psutil.Process().cpu_percent()

        record = TelemetryRecord(
            timestamp=datetime.utcnow(),
            project_id=str(project_id) if project_id else None,
            stage=stage_name,
            duration_seconds=duration,
            cpu_percent=cpu,
            memory_mb=mem,
            gpu_memory_mb=None,
            extra=extra or {},
        )
        self.records.append(record)
        self._persist(record)
        return record

    def _persist(self, record: TelemetryRecord) -> None:
        log_path = settings.ORION_HOME / "telemetry.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"{record.timestamp.isoformat()} | {record.project_id} | {record.stage} | "
                f"{record.duration_seconds:.2f}s | CPU:{record.cpu_percent:.1f}% | MEM:{record.memory_mb:.1f}MB\n"
            )

    def get_summary(self, project_id: UUID | None = None) -> dict[str, Any]:
        """Get telemetry summary."""
        filtered = self.records
        if project_id:
            pid = str(project_id)
            filtered = [r for r in filtered if r.project_id == pid]

        if not filtered:
            return {}

        total_time = sum(r.duration_seconds for r in filtered)
        avg_cpu = sum(r.cpu_percent for r in filtered) / len(filtered)
        max_mem = max(r.memory_mb for r in filtered)

        return {
            "total_stages": len(filtered),
            "total_duration_seconds": total_time,
            "avg_cpu_percent": avg_cpu,
            "peak_memory_mb": max_mem,
            "stages": [{"stage": r.stage, "duration": r.duration_seconds} for r in filtered],
        }
