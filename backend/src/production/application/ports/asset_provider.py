"""Visual-asset acquisition boundary."""

from typing import Protocol

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.domain.scene_plan import ScenePlan


class AssetProviderPort(Protocol):
    """Acquire or generate visual artifacts for one scene."""

    async def acquire_assets(
        self,
        job: ProductionJob,
        scene: ScenePlan,
    ) -> list[Artifact]:
        """Return registered visual artifacts for ``scene``."""
        ...
