"""Semantic Memory implementation using FAISS for vector search."""

import json
import pickle
from pathlib import Path
from uuid import UUID

import numpy as np

from backend.src.learning.semantic_memory.domain.semantic_concept import (
    EmbeddingVector, ISemanticMemory, SemanticConcept,
)
from backend.src.infrastructure.config.settings import settings


class FaissSemanticMemory(ISemanticMemory):
    """Semantic memory backed by FAISS for vector similarity search."""

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = memory_dir or (settings.ORION_HOME / "semantic_memory")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._concepts: dict[str, SemanticConcept] = {}
        self._faiss_available = False
        self._index: Any = None
        self._index_type: str | None = None
        self._dimension: int | None = None
        self._id_map: list[str] = []  # maps faiss index position to concept_id
        self._relations: dict[str, list[tuple[str, str]]] = {}  # concept_id -> [(to_id, relation)]

        self._try_load_faiss()
        self.load()

    def _try_load_faiss(self):
        try:
            import faiss
            self._faiss_available = True
            self._faiss = faiss
        except ImportError:
            self._faiss_available = False

    def _ensure_index(self, dimension: int):
        if not self._faiss_available:
            return
        if self._index is None or self._dimension != dimension:
            self._dimension = dimension
            # Flat index for exact search (good for < 100K vectors)
            self._index = self._faiss.IndexFlatIP(dimension)  # Inner product = cosine if normalized
            self._index_type = "flat_ip"
            # Re-add existing vectors
            if self._concepts:
                vectors = []
                ids = []
                for cid, concept in self._concepts.items():
                    for emb in concept.embeddings:
                        if len(emb.vector) == dimension:
                            vectors.append(emb.vector)
                            ids.append(cid)
                if vectors:
                    self._index.add(np.array(vectors, dtype=np.float32))
                    self._id_map = ids

    def store_concept(self, concept: SemanticConcept) -> None:
        self._concepts[concept.concept_id] = concept
        # Add to FAISS index
        if self._faiss_available:
            for emb in concept.embeddings:
                dim = len(emb.vector)
                self._ensure_index(dim)
                if self._index is not None:
                    vec = np.array([emb.vector], dtype=np.float32)
                    # Normalize for cosine similarity
                    faiss.normalize_L2(vec)
                    self._index.add(vec)
                    self._id_map.append(concept.concept_id)
        self.persist()

    def retrieve_concept(self, concept_id: str) -> SemanticConcept | None:
        return self._concepts.get(concept_id)

    def search_by_embedding(self, embedding: list[float], concept_type: str | None = None, top_k: int = 5) -> list[SemanticConcept]:
        if not self._faiss_available or self._index is None:
            # Fallback to brute force
            return self._brute_force_search(embedding, concept_type, top_k)

        dim = len(embedding)
        self._ensure_index(dim)
        if self._index is None:
            return []

        query = np.array([embedding], dtype=np.float32)
        faiss.normalize_L2(query)
        distances, indices = self._index.search(query, min(top_k * 3, len(self._id_map)))  # oversample

        results = []
        seen = set()
        for idx in indices[0]:
            if idx < 0 or idx >= len(self._id_map):
                continue
            cid = self._id_map[idx]
            if cid in seen:
                continue
            seen.add(cid)
            concept = self._concepts.get(cid)
            if concept and (concept_type is None or concept.concept_type == concept_type):
                results.append(concept)
            if len(results) >= top_k:
                break
        return results

    def _brute_force_search(self, embedding: list[float], concept_type: str | None, top_k: int) -> list[SemanticConcept]:
        query = np.array(embedding, dtype=np.float32)
        scores = []
        for cid, concept in self._concepts.items():
            if concept_type and concept.concept_type != concept_type:
                continue
            for emb in concept.embeddings:
                vec = np.array(emb.vector, dtype=np.float32)
                # Cosine similarity
                score = np.dot(query, vec) / (np.linalg.norm(query) * np.linalg.norm(vec) + 1e-8)
                scores.append((score, concept))
        scores.sort(key=lambda x: x[0], reverse=True)
        seen = set()
        results = []
        for score, concept in scores:
            if concept.concept_id not in seen:
                seen.add(concept.concept_id)
                results.append(concept)
            if len(results) >= top_k:
                break
        return results

    def search_by_label(self, label: str, concept_type: str | None = None) -> list[SemanticConcept]:
        results = []
        label_lower = label.lower()
        for concept in self._concepts.values():
            if concept_type and concept.concept_type != concept_type:
                continue
            if label_lower in concept.label.lower() or label_lower in concept.description.lower():
                results.append(concept)
        return results

    def get_concepts_by_type(self, concept_type: str) -> list[SemanticConcept]:
        return [c for c in self._concepts.values() if c.concept_type == concept_type]

    def link_concepts(self, from_id: str, to_id: str, relation: str) -> None:
        if from_id not in self._relations:
            self._relations[from_id] = []
        self._relations[from_id].append((to_id, relation))
        self.persist()

    def get_related(self, concept_id: str, relation: str | None = None) -> list[SemanticConcept]:
        links = self._relations.get(concept_id, [])
        results = []
        for to_id, rel in links:
            if relation is None or rel == relation:
                concept = self._concepts.get(to_id)
                if concept:
                    results.append(concept)
        return results

    def persist(self) -> None:
        data = {
            "concepts": {cid: self._concept_to_dict(c) for cid, c in self._concepts.items()},
            "relations": self._relations,
        }
        with open(self.memory_dir / "semantic_memory.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        # Save FAISS index separately
        if self._faiss_available and self._index is not None:
            faiss.write_index(self._index, str(self.memory_dir / "faiss.index"))
            with open(self.memory_dir / "id_map.json", "w") as f:
                json.dump(self._id_map, f)

    def load(self) -> None:
        path = self.memory_dir / "semantic_memory.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._concepts = {cid: self._dict_to_concept(c) for cid, c in data.get("concepts", {}).items()}
            self._relations = data.get("relations", {})
        # Load FAISS index
        faiss_path = self.memory_dir / "faiss.index"
        if self._faiss_available and faiss_path.exists():
            self._index = faiss.read_index(str(faiss_path))
            id_map_path = self.memory_dir / "id_map.json"
            if id_map_path.exists():
                with open(id_map_path, "r") as f:
                    self._id_map = json.load(f)

    def _concept_to_dict(self, concept: SemanticConcept) -> dict:
        return {
            "concept_id": concept.concept_id,
            "concept_type": concept.concept_type,
            "label": concept.label,
            "description": concept.description,
            "embeddings": [
                {
                    "vector_id": e.vector_id,
                    "concept_type": e.concept_type,
                    "label": e.label,
                    "vector": e.vector,
                    "source_project": str(e.source_project) if e.source_project else None,
                    "timestamp": e.timestamp,
                    "metadata": e.metadata,
                }
                for e in concept.embeddings
            ],
            "related_concepts": concept.related_concepts,
            "occurrences": concept.occurrences,
            "confidence": concept.confidence,
        }

    def _dict_to_concept(self, data: dict) -> SemanticConcept:
        return SemanticConcept(
            concept_id=data["concept_id"],
            concept_type=data["concept_type"],
            label=data["label"],
            description=data["description"],
            embeddings=[
                EmbeddingVector(
                    vector_id=e["vector_id"],
                    concept_type=e["concept_type"],
                    label=e["label"],
                    vector=e["vector"],
                    source_project=UUID(e["source_project"]) if e.get("source_project") else None,
                    timestamp=e.get("timestamp"),
                    metadata=e.get("metadata"),
                )
                for e in data.get("embeddings", [])
            ],
            related_concepts=data.get("related_concepts", []),
            occurrences=[(UUID(o[0]), o[1]) for o in data.get("occurrences", [])],
            confidence=data.get("confidence", 0.5),
        )
