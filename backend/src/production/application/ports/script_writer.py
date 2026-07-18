"""Script-writing boundary."""

from dataclasses import dataclass
from typing import Protocol

from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.production_plan import ProductionPlan


@dataclass(frozen=True, slots=True)
class ScriptDraft:
    """Versioned script output passed to downstream scene planning."""

    schema_version: str
    text: str
    language: str


class ScriptWriterPort(Protocol):
    """Produce a script without coupling the application to a provider."""

    async def write_script(
        self,
        job: ProductionJob,
        plan: ProductionPlan,
    ) -> ScriptDraft:
        """Return the script for a validated production plan."""
        ...
