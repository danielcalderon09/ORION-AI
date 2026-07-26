"""Memory Manager with streaming and automatic resource cleanup."""

import gc
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass
class MemoryBudget:
    """Memory limits for a processing session."""

    max_ram_mb: int = 4096
    max_vram_mb: int | None = None  # None = no GPU limit
    frame_buffer_max_mb: int = 512
    auto_evict_after_stage: bool = True


class StreamingBuffer:
    """Manages frame streaming without loading entire video into RAM."""

    def __init__(self, max_size_mb: int = 512):
        self.max_size_mb = max_size_mb
        self._buffer: dict[int, Any] = {}
        self._access_count: dict[int, int] = {}
        self._total_size_mb = 0.0

    def get_frame(self, frame_idx: int, loader: Callable[[int], Any]) -> Any:
        """Get a frame, loading it if not in buffer."""
        if frame_idx in self._buffer:
            self._access_count[frame_idx] += 1
            return self._buffer[frame_idx]

        frame = loader(frame_idx)
        self._add(frame_idx, frame)
        return frame

    def _add(self, frame_idx: int, frame: Any) -> None:
        # Estimate size (rough)
        size_mb = self._estimate_size(frame)
        if self._total_size_mb + size_mb > self.max_size_mb:
            self._evict_lru()

        self._buffer[frame_idx] = frame
        self._access_count[frame_idx] = 1
        self._total_size_mb += size_mb

    def _evict_lru(self) -> None:
        if not self._buffer:
            return
        # Evict least recently used
        lru = min(self._access_count, key=self._access_count.get)
        evicted = self._buffer.pop(lru, None)
        if evicted is not None:
            self._total_size_mb -= self._estimate_size(evicted)
        self._access_count.pop(lru, None)

    def _estimate_size(self, obj: Any) -> float:
        try:
            import numpy as np

            if isinstance(obj, np.ndarray):
                return obj.nbytes / (1024 * 1024)
        except ImportError:
            pass
        return 1.0  # default estimate

    def clear(self) -> None:
        self._buffer.clear()
        self._access_count.clear()
        self._total_size_mb = 0.0
        gc.collect()


class MemoryManager:
    """Central memory management for Orion AI."""

    def __init__(self, budget: MemoryBudget | None = None):
        self.budget = budget or MemoryBudget()
        self._stage_buffers: dict[str, StreamingBuffer] = {}
        self._process = psutil.Process()
        self._callbacks: list[Callable[[str, float], None]] = []

    def register_callback(self, callback: Callable[[str, float], None]) -> None:
        """Register a callback for memory pressure events."""
        self._callbacks.append(callback)

    def get_buffer(self, stage_name: str) -> StreamingBuffer:
        """Get or create a streaming buffer for a stage."""
        if stage_name not in self._stage_buffers:
            self._stage_buffers[stage_name] = StreamingBuffer(
                max_size_mb=self.budget.frame_buffer_max_mb
            )
        return self._stage_buffers[stage_name]

    def evict_stage(self, stage_name: str) -> None:
        """Clear buffers for a completed stage."""
        buffer = self._stage_buffers.pop(stage_name, None)
        if buffer is not None:
            buffer.clear()

    def check_pressure(self) -> dict[str, float]:
        """Check current memory pressure."""
        mem = self._process.memory_info()
        ram_used_mb = mem.rss / (1024 * 1024)
        ram_percent = ram_used_mb / self.budget.max_ram_mb

        vram_info = self._get_vram_info()
        vram_percent = None
        if vram_info and self.budget.max_vram_mb:
            vram_percent = vram_info["used_mb"] / self.budget.max_vram_mb

        status = {
            "ram_used_mb": ram_used_mb,
            "ram_percent": ram_percent,
            "vram_used_mb": vram_info.get("used_mb") if vram_info else None,
            "vram_percent": vram_percent,
        }

        # Trigger callbacks if pressure is high
        if ram_percent > 0.85:
            for cb in self._callbacks:
                cb("ram", ram_percent)
        if vram_percent and vram_percent > 0.90:
            for cb in self._callbacks:
                cb("vram", vram_percent)

        return status

    def _get_vram_info(self) -> dict[str, float] | None:
        """Get GPU VRAM info if available."""
        try:
            import torch

            if torch.cuda.is_available():
                reserved = torch.cuda.memory_reserved() / (1024 * 1024)
                allocated = torch.cuda.memory_allocated() / (1024 * 1024)
                return {"used_mb": allocated, "reserved_mb": reserved}
        except ImportError:
            pass
        return None

    def force_gc(self) -> None:
        """Force garbage collection."""
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def start_session(self, project_id: str) -> None:
        """Begin a processing session for a project."""
        self._session_id = project_id
        self._stage_buffers.clear()
        gc.collect()

    def end_session(self, project_id: str) -> None:
        """End a processing session and release resources."""
        self._stage_buffers.clear()
        self.force_gc()
        self._session_id = None

    def request_allocation(self, project_id: str, stage_name: str, requested_mb: float) -> bool:
        """Request memory allocation for a stage. Returns True if granted."""
        pressure = self.check_pressure()
        if pressure["ram_percent"] > 0.90:
            self.force_gc()
            pressure = self.check_pressure()
        return pressure["ram_percent"] < 0.95

    def get_status(self) -> dict[str, Any]:
        """Get full memory status."""
        pressure = self.check_pressure()
        return {
            **pressure,
            "active_buffers": len(self._stage_buffers),
            "budget": {
                "max_ram_mb": self.budget.max_ram_mb,
                "max_vram_mb": self.budget.max_vram_mb,
                "frame_buffer_max_mb": self.budget.frame_buffer_max_mb,
            },
        }
