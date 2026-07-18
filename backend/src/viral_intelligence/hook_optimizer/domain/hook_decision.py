"""Hook Optimizer interfaces and domain model."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class HookDecision:
    """Decision about how to optimize the hook of a clip."""
    clip_id: str
    original_start: float
    optimized_start: float
    hook_duration: float  # typically 0-3 seconds
    hook_score: float  # how strong the chosen hook is (0-1)
    strategy: str  # "jump_to_peak", "trim_silence", "start_with_action", "start_with_reaction"
    justification: str
    risk_level: str  # low, medium, high — risk of losing narrative coherence


class IHookOptimizer(Protocol):
    """Optimizes the opening seconds of a clip."""
    async def optimize(self, clip_start: float, clip_end: float, features: dict[str, Any]) -> HookDecision: ...


class IHookStrategyProvider(Protocol):
    """Provider for a specific hook optimization strategy."""
    async def evaluate(self, candidate_start: float, features: dict[str, Any]) -> float: ...  # returns hook score
