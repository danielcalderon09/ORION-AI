"""Critic AI domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CriticScore:
    """Score on a specific quality axis."""
    axis: str  # narrative, technical, retention, engagement
    score: float  # 0-1
    issues: list[str]
    strengths: list[str]


@dataclass
class CritiqueReport:
    """Full critique of a clip candidate."""
    candidate_id: str
    overall_score: float
    axis_scores: list[CriticScore]
    fatal_flaws: list[str]  # issues that disqualify the candidate
    recommendations: list[str]
    confidence: float


class ICriticProvider(Protocol):
    """Provider for a specific quality axis critique."""
    async def critique(self, candidate: dict[str, Any], context: dict[str, Any]) -> CriticScore: ...


class ICriticAI(Protocol):
    """Orchestrates multiple critics into a full report."""
    async def critique_candidate(self, candidate: dict[str, Any], context: dict[str, Any]) -> CritiqueReport: ...
