"""Scene-planning boundary."""

from typing import Protocol

from backend.src.production.application.ports.script_writer import ScriptDraft
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.production_plan import ProductionPlan
from backend.src.production.domain.scene_plan import ScenePlan


class ScenePlannerPort(Protocol):
    """Convert a plan and script into ordered scenes."""

    async def create_scenes(
        self,
        job: ProductionJob,
        plan: ProductionPlan,
        script: ScriptDraft,
    ) -> list[ScenePlan]:
        """Return complete, validated scene contracts."""
        ...
