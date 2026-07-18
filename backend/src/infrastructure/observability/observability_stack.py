"""Observability stack for metrics, events, and telemetry."""
from typing import Any, Dict, Optional
import time


class ObservabilityStack:
    """Collects pipeline- and agent-level metrics and events."""

    def __init__(self) -> None:
        self._events: list[Dict[str, Any]] = []
        self._pipeline_start_ts: Optional[float] = None

    def start_pipeline(self, project_id: str) -> None:
        self._pipeline_start_ts = time.time()
        self.emit_event("pipeline.started", {"project_id": project_id})

    def end_pipeline(self, project_id: str, success: bool = True) -> None:
        duration = None
        if self._pipeline_start_ts is not None:
            duration = time.time() - self._pipeline_start_ts
        self.emit_event("pipeline.ended", {
            "project_id": project_id,
            "success": success,
            "duration_seconds": duration,
        })

    def emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self._events.append({
            "timestamp": time.time(),
            "type": event_type,
            "payload": payload,
        })

    def get_events(self) -> list[Dict[str, Any]]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()
        self._pipeline_start_ts = None
