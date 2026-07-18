"""Creative Memory domain model."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass
class CreativePattern:
    """A reusable creative decision pattern."""
    pattern_id: str
    category: str  # gaming, podcast, tutorial, etc.
    platform: str
    decision_type: str  # hook, pacing, cut_point, zoom, subtitle
    context_features: dict[str, Any]  # what content features triggered this
    action: dict[str, Any]  # what was done
    outcome: dict[str, Any]  # viral_score, retention, user_rating
    usage_count: int
    success_rate: float  # 0-1


class ICreativeMemory(Protocol):
    """Stores and retrieves reusable creative patterns."""
    def store_pattern(self, pattern: CreativePattern) -> None: ...
    def find_patterns(self, category: str, platform: str, decision_type: str | None = None) -> list[CreativePattern]: ...
    def get_best_practice(self, category: str, platform: str, decision_type: str) -> CreativePattern | None: ...
    def update_outcome(self, pattern_id: str, outcome: dict[str, Any]) -> None: ...
    def persist(self) -> None: ...
    def load(self) -> None: ...
