"""Semantic Memory interfaces for storing and retrieving reusable semantic concepts."""

from typing import Any, Protocol
from dataclasses import dataclass
from uuid import UUID


@dataclass
class EmbeddingVector:
    """A named embedding with metadata."""
    vector_id: str
    concept_type: str  # character, scene, action, object, event
    label: str
    vector: list[float]
    source_project: UUID | None = None
    timestamp: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class SemanticConcept:
    """An extracted semantic concept with embeddings and relationships."""
    concept_id: str
    concept_type: str
    label: str
    description: str
    embeddings: list[EmbeddingVector]
    related_concepts: list[str]
    occurrences: list[tuple[UUID, float]]  # (project_id, timestamp)
    confidence: float


class ISemanticMemory(Protocol):
    """Port for semantic memory storage and retrieval."""

    def store_concept(self, concept: SemanticConcept) -> None: ...
    def retrieve_concept(self, concept_id: str) -> SemanticConcept | None: ...
    def search_by_embedding(self, embedding: list[float], concept_type: str | None = None, top_k: int = 5) -> list[SemanticConcept]: ...
    def search_by_label(self, label: str, concept_type: str | None = None) -> list[SemanticConcept]: ...
    def get_concepts_by_type(self, concept_type: str) -> list[SemanticConcept]: ...
    def link_concepts(self, from_id: str, to_id: str, relation: str) -> None: ...
    def get_related(self, concept_id: str, relation: str | None = None) -> list[SemanticConcept]: ...
    def persist(self) -> None: ...
    def load(self) -> None: ...
