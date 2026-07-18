"""Event Graph domain model with causal relationships."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass
class EventNode:
    """An event in the Event Graph."""
    event_id: str
    event_type: str  # action, reaction, scene_change, audio_peak, speech_act, etc.
    start_time: float
    end_time: float
    description: str
    confidence: float
    participants: list[str]  # identity_ids
    source_agent: str  # which agent detected this
    properties: dict[str, Any]


@dataclass
class CausalEdge:
    """A causal relationship between events."""
    from_event: str
    to_event: str
    relation_type: str  # causes, enables, follows, reacts_to, contradicts
    confidence: float
    evidence: list[str]  # supporting evidence references


class IEventGraph(Protocol):
    """Port for causal event graph operations."""

    def add_event(self, event: EventNode) -> None: ...
    def add_causal_link(self, edge: CausalEdge) -> None: ...
    def get_event(self, event_id: str) -> EventNode | None: ...
    def query_causes(self, event_id: str, depth: int = 1) -> list[EventNode]: ...
    def query_effects(self, event_id: str, depth: int = 1) -> list[EventNode]: ...
    def query_by_type(self, event_type: str, time_range: tuple[float, float] | None = None) -> list[EventNode]: ...
    def query_chain(self, from_event: str, to_event: str) -> list[list[EventNode]]: ...  # paths between events
    def get_temporal_sequence(self, start: float, end: float) -> list[EventNode]: ...
    def persist(self, project_id: UUID) -> None: ...
    def load(self, project_id: UUID) -> None: ...
