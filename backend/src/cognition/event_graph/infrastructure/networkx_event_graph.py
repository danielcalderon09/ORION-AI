"""Event Graph implementation with causal relationships."""

import json
from pathlib import Path
from uuid import UUID

import networkx as nx

from backend.src.cognition.event_graph.domain.event_node import (
    CausalEdge, EventNode, IEventGraph,
)
from backend.src.infrastructure.config.settings import settings


class NetworkXEventGraph(IEventGraph):
    """Event graph implementation using NetworkX with causal edges."""

    def __init__(self):
        self.graph = nx.DiGraph()
        self.project_id: str | None = None

    def add_event(self, event: EventNode) -> None:
        self.graph.add_node(
            event.event_id,
            event_type=event.event_type,
            start_time=event.start_time,
            end_time=event.end_time,
            description=event.description,
            confidence=event.confidence,
            participants=event.participants,
            source_agent=event.source_agent,
            **event.properties,
        )

    def add_causal_link(self, edge: CausalEdge) -> None:
        self.graph.add_edge(
            edge.from_event,
            edge.to_event,
            relation_type=edge.relation_type,
            confidence=edge.confidence,
            evidence=edge.evidence,
        )

    def get_event(self, event_id: str) -> EventNode | None:
        if event_id not in self.graph:
            return None
        attrs = self.graph.nodes[event_id]
        return EventNode(
            event_id=event_id,
            event_type=attrs.get("event_type", ""),
            start_time=attrs.get("start_time", 0),
            end_time=attrs.get("end_time", 0),
            description=attrs.get("description", ""),
            confidence=attrs.get("confidence", 0),
            participants=attrs.get("participants", []),
            source_agent=attrs.get("source_agent", ""),
            properties={k: v for k, v in attrs.items() if k not in {
                "event_type", "start_time", "end_time", "description",
                "confidence", "participants", "source_agent",
            }},
        )

    def query_causes(self, event_id: str, depth: int = 1) -> list[EventNode]:
        """Find events that directly or indirectly caused the given event."""
        if event_id not in self.graph:
            return []
        causes = []
        visited = set()
        queue = [(event_id, 0)]
        while queue:
            current, level = queue.pop(0)
            if current in visited or level > depth:
                continue
            visited.add(current)
            for predecessor in self.graph.predecessors(current):
                edge_data = self.graph.get_edge_data(predecessor, current)
                if edge_data and edge_data.get("relation_type") in {"causes", "enables"}:
                    node = self.get_event(predecessor)
                    if node:
                        causes.append(node)
                    queue.append((predecessor, level + 1))
        return causes

    def query_effects(self, event_id: str, depth: int = 1) -> list[EventNode]:
        """Find events that directly or indirectly resulted from the given event."""
        if event_id not in self.graph:
            return []
        effects = []
        visited = set()
        queue = [(event_id, 0)]
        while queue:
            current, level = queue.pop(0)
            if current in visited or level > depth:
                continue
            visited.add(current)
            for successor in self.graph.successors(current):
                edge_data = self.graph.get_edge_data(current, successor)
                if edge_data and edge_data.get("relation_type") in {"causes", "enables", "follows"}:
                    node = self.get_event(successor)
                    if node:
                        effects.append(node)
                    queue.append((successor, level + 1))
        return effects

    def query_by_type(self, event_type: str, time_range: tuple[float, float] | None = None) -> list[EventNode]:
        results = []
        for node_id in self.graph.nodes():
            attrs = self.graph.nodes[node_id]
            if attrs.get("event_type") == event_type:
                if time_range is None or (time_range[0] <= attrs.get("start_time", 0) <= time_range[1]):
                    node = self.get_event(node_id)
                    if node:
                        results.append(node)
        return results

    def query_chain(self, from_event: str, to_event: str) -> list[list[EventNode]]:
        """Find all causal paths between two events."""
        try:
            paths = list(nx.all_simple_paths(self.graph, from_event, to_event, cutoff=5))
            return [[self.get_event(eid) for eid in path] for path in paths]
        except nx.NetworkXNoPath:
            return []

    def get_temporal_sequence(self, start: float, end: float) -> list[EventNode]:
        events = []
        for node_id in self.graph.nodes():
            attrs = self.graph.nodes[node_id]
            t = attrs.get("start_time", 0)
            if start <= t <= end:
                node = self.get_event(node_id)
                if node:
                    events.append(node)
        events.sort(key=lambda e: e.start_time)
        return events

    def persist(self, project_id: UUID) -> None:
        path = self._get_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self.graph)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)

    def load(self, project_id: UUID) -> None:
        path = self._get_path(project_id)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.graph = nx.node_link_graph(data)
        else:
            self.graph = nx.DiGraph()
        self.project_id = str(project_id)

    def _get_path(self, project_id: UUID) -> Path:
        return settings.PROJECTS_DIR / str(project_id) / "knowledge" / "event_graph.json"
