"""Narration-generation boundary."""

from typing import Protocol

from backend.src.production.application.ports.script_writer import ScriptDraft
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.production_plan import ProductionPlan


class NarrationProviderPort(Protocol):
    """Produce a narration audio artifact."""

    async def generate_narration(
        self,
        job: ProductionJob,
        plan: ProductionPlan,
        script: ScriptDraft,
    ) -> Artifact:
        """Return the registered narration artifact."""
        ...
