"""Planning boundary for prompt-to-video production."""

from typing import Protocol

from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.production_plan import ProductionPlan


class PlannerPort(Protocol):
    """Create a validated production plan from a production job."""

    async def create_plan(self, job: ProductionJob) -> ProductionPlan:
        """Return the creative and technical plan for ``job``."""
        ...
