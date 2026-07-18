"""Retention Simulator interfaces and domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RetentionPoint:
    """Retention estimate at a specific point in time."""
    time_offset: float  # seconds from clip start
    retained_viewers_pct: float  # 0.0 - 1.0
    drop_risk: str  # none, low, medium, high
    suggested_action: str | None  # e.g., "jump_cut", "speed_up", "add_hook"


@dataclass
class RetentionCurve:
    """Estimated retention curve for a clip."""
    clip_id: str
    points: list[RetentionPoint]
    average_retention: float
    critical_drop_points: list[float]  # timestamps where major drops occur
    estimated_avg_watch_pct: float  # estimated % of clip watched on average


class IRetentionSimulator(Protocol):
    """Simulates viewer retention for a given clip edit."""
    async def simulate(self, clip_start: float, clip_end: float, features: dict[str, Any]) -> RetentionCurve: ...


class IRetentionModelProvider(Protocol):
    """Provider for a specific retention prediction model."""
    async def predict(self, timeline_features: dict[str, Any]) -> list[RetentionPoint]: ...
