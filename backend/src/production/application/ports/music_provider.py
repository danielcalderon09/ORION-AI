"""Music acquisition and generation boundary."""

from typing import Protocol

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.production_plan import ProductionPlan


class MusicProviderPort(Protocol):
    """Prepare music for a production without fixing a provider."""

    async def prepare_music(
        self,
        job: ProductionJob,
        plan: ProductionPlan,
    ) -> Artifact:
        """Return the registered music artifact."""
        ...
