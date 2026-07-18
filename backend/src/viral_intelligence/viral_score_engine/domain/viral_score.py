"""Viral Score Engine interfaces and domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ViralFactor:
    """A single factor contributing to viral potential."""
    factor_name: str
    score: float  # 0.0 - 1.0
    weight: float  # contribution weight in composite
    description: str
    evidence: list[dict[str, Any]]


@dataclass
class ViralScore:
    """Composite viral score for a temporal segment."""
    segment_start: float
    segment_end: float
    composite_score: float
    factors: list[ViralFactor]
    confidence: float  # model confidence in this score
    platform_fit: dict[str, float]  # per-platform fit score


@dataclass
class ViralScoreMap:
    """Viral scores across the entire video timeline."""
    scores: list[ViralScore]
    peak_segments: list[ViralScore]
    average_score: float
    best_platform: str


class IViralScoreProvider(Protocol):
    """Provider for a specific viral factor."""
    async def calculate(self, features: dict[str, Any]) -> ViralFactor: ...


class IViralScoreEngine(Protocol):
    """Engine that composes multiple viral factors into a score map."""
    async def score_segment(self, segment: tuple[float, float], features: dict[str, Any]) -> ViralScore: ...
    async def score_video(self, features: dict[str, Any]) -> ViralScoreMap: ...
