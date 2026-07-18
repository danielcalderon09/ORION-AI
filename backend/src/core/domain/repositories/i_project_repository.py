"""Repository interfaces (ports) for persistence."""

from typing import Protocol
from uuid import UUID

from backend.src.core.domain.entities.video_project import ProjectBrain, VideoProject


class IProjectRepository(Protocol):
    """Port for project persistence."""

    def save(self, project: VideoProject) -> None: ...
    def get_by_id(self, project_id: UUID) -> VideoProject | None: ...
    def get_all(self) -> list[VideoProject]: ...
    def delete(self, project_id: UUID) -> None: ...


class IFeatureStore(Protocol):
    """Port for feature storage and retrieval."""

    def save(
        self,
        project_id: UUID,
        agent_id: str,
        feature_name: str,
        data: object,
        version: str = "1.0",
    ) -> str: ...

    def load(
        self,
        project_id: UUID,
        agent_id: str,
        feature_name: str,
        version: str = "1.0",
    ) -> object: ...

    def exists(
        self,
        project_id: UUID,
        agent_id: str,
        feature_name: str,
        version: str = "1.0",
    ) -> bool: ...

    def list_features(self, project_id: UUID) -> list[dict]: ...


class IKnowledgeGraph(Protocol):
    """Port for knowledge graph operations."""

    def add_node(self, node_type: str, node_id: str, properties: dict) -> None: ...
    def add_edge(
        self, from_id: str, to_id: str, relation: str, properties: dict | None = None
    ) -> None: ...
    def query(self, query_spec: dict) -> list[dict]: ...
    def get_subgraph(self, center_node_id: str, depth: int = 2) -> dict: ...
    def persist(self, project_id: UUID) -> None: ...
    def load(self, project_id: UUID) -> None: ...


class IMediaCache(Protocol):
    """Port for media file caching (frames, audio, temp)."""

    def cache_frames(
        self, project_id: UUID, frames: list[object], metadata: dict
    ) -> str: ...
    def cache_audio(self, project_id: UUID, audio_path: str, metadata: dict) -> str: ...
    def get_cached_path(self, project_id: UUID, cache_type: str, identifier: str) -> str | None: ...
    def clear_project_cache(self, project_id: UUID) -> None: ...
