"""Boundary to the existing video-to-clips capability."""

from typing import Protocol
from uuid import UUID

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob


class ClipHandoffPort(Protocol):
    """Submit a validated long-form artifact to the existing clip flow."""

    async def submit(
        self,
        job: ProductionJob,
        long_form_artifact: Artifact,
    ) -> UUID:
        """Return the existing clip project's UUID."""
        ...
