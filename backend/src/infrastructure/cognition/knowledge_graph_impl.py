"""In-memory Knowledge Graph implementation using NetworkX."""

import json
from pathlib import Path
from uuid import UUID

import networkx as nx

from backend.src.core.domain.repositories.i_project_repository import IKnowledgeGraph


class InMemoryKnowledgeGraph(IKnowledgeGraph):
    """NetworkX-based knowledge graph for MVP."""

    def __init__(self):
        self.graph = nx.DiGraph()
        self.project_id: str | None = None

    def add_node(self, node_type: str, node_id: str, properties: dict) -> None:
        self.graph.add_node(node_id, node_type=node_type, **properties)

    def add_edge(self, from_id: str, to_id: str, relation: str, properties: dict | None = None) -> None:
        props = properties or {}
        self.graph.add_edge(from_id, to_id, relation=relation, **props)

    def query(self, query_spec: dict) -> list[dict]:
        """Simple query by node type and properties."""
        results = []
        node_type = query_spec.get("node_type")
        for node_id, attrs in self.graph.nodes(data=True):
            if node_type and attrs.get("node_type") != node_type:
                continue
            match = True
            for key, value in query_spec.get("properties", {}).items():
                if attrs.get(key) != value:
                    match = False
                    break
            if match:
                results.append({"id": node_id, **attrs})
        return results

    def get_subgraph(self, center_node_id: str, depth: int = 2) -> dict:
        """Extract subgraph around a center node."""
        if center_node_id not in self.graph:
            return {"nodes": [], "edges": []}

        nodes = {center_node_id}
        edges = []
        current = {center_node_id}

        for _ in range(depth):
            next_level = set()
            for node in current:
                for neighbor in self.graph.neighbors(node):
                    edge_data = self.graph.get_edge_data(node, neighbor)
                    edges.append({
                        "from": node,
                        "to": neighbor,
                        **(edge_data or {}),
                    })
                    next_level.add(neighbor)
            nodes.update(next_level)
            current = next_level

        node_data = []
        for n in nodes:
            node_data.append({"id": n, **self.graph.nodes[n]})

        return {"nodes": node_data, "edges": edges}

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
        from backend.src.infrastructure.config.settings import settings
        return settings.PROJECTS_DIR / str(project_id) / "knowledge" / "knowledge_graph.json"
