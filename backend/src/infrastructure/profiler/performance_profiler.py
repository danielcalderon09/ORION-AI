"""Performance Profiler for per-stage pipeline instrumentation."""

import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

import psutil


@dataclass
class StageProfile:
    """Profiling data for a single pipeline stage."""
    stage_name: str
    project_id: str
    start_time: float
    end_time: float = 0.0
    duration_seconds: float = 0.0
    cpu_percent_avg: float = 0.0
    memory_mb_start: float = 0.0
    memory_mb_peak: float = 0.0
    memory_mb_end: float = 0.0
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0
    agent_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class PerformanceProfiler:
    """Hierarchical profiler for the Orion pipeline."""

    def __init__(self):
        self._active: dict[str, StageProfile] = {}
        self._completed: list[StageProfile] = []
        self._process = psutil.Process()
        self._global_start: float | None = None

    def start_stage(self, stage_name: str, project_id: UUID, agent_id: str = "") -> str:
        """Begin profiling a stage. Returns a handle for end_stage."""
        handle = f"{project_id}:{stage_name}"
        mem_start = self._process.memory_info().rss / (1024 * 1024)
        io_start = self._process.io_counters()

        self._active[handle] = StageProfile(
            stage_name=stage_name,
            project_id=str(project_id),
            start_time=time.perf_counter(),
            memory_mb_start=mem_start,
            agent_id=agent_id,
            metadata={"io_read_start": io_start.read_bytes, "io_write_start": io_start.write_bytes},
        )

        if self._global_start is None:
            self._global_start = time.perf_counter()
        return handle

    def end_stage(self, handle: str, metadata: dict[str, Any] | None = None) -> StageProfile:
        """End profiling a stage and return the profile."""
        profile = self._active.pop(handle, None)
        if profile is None:
            raise ValueError(f"No active stage for handle: {handle}")

        profile.end_time = time.perf_counter()
        profile.duration_seconds = profile.end_time - profile.start_time

        # Memory
        mem_info = self._process.memory_info()
        profile.memory_mb_end = mem_info.rss / (1024 * 1024)
        profile.memory_mb_peak = max(profile.memory_mb_start, profile.memory_mb_end)

        # CPU (approximate since start)
        profile.cpu_percent_avg = self._process.cpu_percent()

        # I/O delta
        io_end = self._process.io_counters()
        profile.io_read_mb = (io_end.read_bytes - profile.metadata.get("io_read_start", 0)) / (1024 * 1024)
        profile.io_write_mb = (io_end.write_bytes - profile.metadata.get("io_write_start", 0)) / (1024 * 1024)

        if metadata:
            profile.metadata.update(metadata)

        self._completed.append(profile)
        return profile

    def get_summary(self, project_id: UUID | None = None) -> dict[str, Any]:
        """Get profiling summary, optionally filtered by project."""
        profiles = self._completed
        if project_id:
            pid = str(project_id)
            profiles = [p for p in profiles if p.project_id == pid]

        if not profiles:
            return {}

        total_time = sum(p.duration_seconds for p in profiles)
        peak_mem = max(p.memory_mb_peak for p in profiles)
        total_io_read = sum(p.io_read_mb for p in profiles)
        total_io_write = sum(p.io_write_mb for p in profiles)

        stages = []
        for p in profiles:
            stages.append({
                "stage": p.stage_name,
                "agent": p.agent_id,
                "duration_sec": round(p.duration_seconds, 3),
                "cpu_percent": round(p.cpu_percent_avg, 1),
                "mem_peak_mb": round(p.memory_mb_peak, 1),
                "mem_delta_mb": round(p.memory_mb_end - p.memory_mb_start, 1),
                "io_read_mb": round(p.io_read_mb, 1),
                "io_write_mb": round(p.io_write_mb, 1),
            })

        return {
            "total_stages": len(profiles),
            "total_time_sec": round(total_time, 3),
            "peak_memory_mb": round(peak_mem, 1),
            "total_io_read_mb": round(total_io_read, 1),
            "total_io_write_mb": round(total_io_write, 1),
            "stages": stages,
        }

    def reset(self) -> None:
        """Clear all profiles."""
        self._active.clear()
        self._completed.clear()
        self._global_start = None

    def stage_decorator(self, stage_name: str, agent_id: str = ""):
        """Decorator to automatically profile a function."""
        def decorator(func: Callable) -> Callable:
            async def wrapper(*args, **kwargs):
                # Assume first arg is project_id if available
                project_id = kwargs.get("project_id", UUID(int=0))
                handle = self.start_stage(stage_name, project_id, agent_id)
                try:
                    return await func(*args, **kwargs)
                finally:
                    self.end_stage(handle)
            return wrapper
        return decorator
