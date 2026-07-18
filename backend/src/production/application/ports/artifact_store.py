"""Artifact registry and storage boundary."""

from typing import Protocol
from uuid import UUID

from backend.src.production.domain.artifact import Artifact


class ArtifactStorePort(Protocol):
    """Persist and retrieve artifact records without exposing storage details."""

    async def save(self, artifact: Artifact) -> Artifact:
        """Create or update an artifact record idempotently."""
        ...

    async def get(self, artifact_id: UUID) -> Artifact | None:
        """Return an artifact by ID, if present."""
        ...

    async def list_for_job(self, job_id: UUID) -> list[Artifact]:
        """Return all registered artifacts for a job."""
        ...
