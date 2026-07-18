"""Explainability domain model for decision justification."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass
class ExplanationFactor:
    """A single factor that influenced a decision."""
    factor_name: str
    factor_type: str  # attention, narrative, emotional, technical, user_preference
    weight: float
    score: float
    description: str
    evidence: list[dict[str, Any]]  # supporting data points


@dataclass
class DecisionExplanation:
    """Full explanation for a Director AI decision."""
    explanation_id: UUID
    project_id: UUID
    clip_id: str
    decision_type: str  # clip_selection, timing, pacing, framing, subtitle
    timestamp: float
    overall_confidence: float
    factors: list[ExplanationFactor]
    reasoning_chain: list[str]  # human-readable steps
    alternatives_considered: list[dict[str, Any]]
    summary: str  # one-line human-readable justification


@dataclass
class ExplainabilityReport:
    """Complete explainability report for a project."""
    project_id: UUID
    clip_explanations: list[DecisionExplanation]
    pipeline_decisions: list[dict[str, Any]]
    narrative_justification: str
    attention_highlights: list[dict[str, Any]]
