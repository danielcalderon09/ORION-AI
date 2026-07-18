"""Multi Candidate Generator domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CandidateVariant:
    """A single variant of a clip."""
    variant_id: str
    parent_clip_id: str
    start: float
    end: float
    hook_strategy: str
    pacing_profile: str
    subtitle_style: str
    has_zoom: bool
    has_jump_cut: bool
    estimated_viral_score: float
    estimated_retention: float


@dataclass
class CandidateSet:
    """Set of candidates for a single source moment."""
    source_moment_id: str
    candidates: list[CandidateVariant]
    generation_strategy: str


class ICandidateStrategy(Protocol):
    """Strategy for generating a specific type of candidate variant."""
    async def generate(self, base_clip: dict[str, Any], context: dict[str, Any]) -> CandidateVariant: ...


class IMultiCandidateGenerator(Protocol):
    """Generates multiple candidates per source moment."""
    async def generate_candidates(self, selected_clips: list[dict], context: dict[str, Any]) -> list[CandidateSet]: ...
