"""Temporal Identity Tracking interfaces."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass
class TemporalIdentity:
    """A persistent identity tracked across time."""
    identity_id: str
    entity_type: str  # person, object, animal, vehicle, etc.
    first_seen: float  # timestamp
    last_seen: float
    appearance_count: int
    visual_signature: list[float] | None  # embedding or feature vector
    trajectory: list[tuple[float, float, float]]  # (timestamp, x, y) normalized
    state_history: list[dict[str, Any]]  # changes over time
    representative_frames: list[str]  # frame references


@dataclass
class IdentityMatch:
    """Result of matching an observation to known identities."""
    identity_id: str | None
    confidence: float
    match_reason: str
    candidates: list[tuple[str, float]]  # (identity_id, score)


class ITemporalTracker(Protocol):
    """Port for temporal identity tracking across video frames."""

    def register_observation(self, timestamp: float, bbox: tuple, visual_features: list[float], entity_type: str) -> IdentityMatch: ...
    def get_identity(self, identity_id: str) -> TemporalIdentity | None: ...
    def get_active_identities(self, time_window: tuple[float, float] | None = None) -> list[TemporalIdentity]: ...
    def get_trajectory(self, identity_id: str) -> list[tuple[float, float, float]]: ...
    def merge_identities(self, id_a: str, id_b: str) -> str: ...
    def persist(self, project_id: UUID) -> None: ...
    def load(self, project_id: UUID) -> None: ...
