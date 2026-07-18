"""Reflection Engine domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ImprovementSuggestion:
    """A concrete suggestion to improve a clip."""
    suggestion_id: str
    category: str  # hook, pacing, trim, subtitle, audio, visual
    severity: str  # minor, moderate, critical
    description: str
    current_value: Any
    proposed_value: Any
    expected_impact: str  # e.g., "+12% retention"
    confidence: float  # 0-1


@dataclass
class ReflectionReport:
    """Output of the Reflection Engine for a single clip."""
    clip_id: str
    overall_quality: float  # 0-1
    suggestions: list[ImprovementSuggestion]
    missed_opportunities: list[str]
    alignment_score: float  # how well clip matches creative brief


class IReflectionEngine(Protocol):
    """Analyzes clips and proposes improvements."""
    async def reflect(self, clip_data: dict[str, Any], creative_brief: dict[str, Any], metrics: dict[str, Any]) -> ReflectionReport: ...
