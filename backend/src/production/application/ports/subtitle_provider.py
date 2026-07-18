"""Subtitle-generation boundary."""

from typing import Protocol

from backend.src.production.application.ports.script_writer import ScriptDraft
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.production_plan import ProductionPlan


class SubtitleProviderPort(Protocol):
    """Produce timed subtitles from a script and narration."""

    async def generate_subtitles(
        self,
        job: ProductionJob,
        plan: ProductionPlan,
        script: ScriptDraft,
        narration: Artifact,
    ) -> Artifact:
        """Return the registered subtitle artifact."""
        ...
