"""Pure Structured Output prompt construction from ProductionScenePlan only."""

import json
from typing import cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProviderRequest,
)


class VisualAssetPlanningPrompt(ContractModel):
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    system: str
    user: str
    response_schema: dict[str, object]


class VisualAssetPlanningPromptBuilder:
    visual_asset_planning_prompt_version = "1.0.0"

    def __init__(self, *, max_scene_plan_bytes: int) -> None:
        if max_scene_plan_bytes < 1:
            raise ValueError("maximum scene-plan prompt size must be positive")
        self._max_scene_plan_bytes = max_scene_plan_bytes

    def build(
        self,
        request: VisualAssetPlanningProviderRequest,
    ) -> VisualAssetPlanningPrompt:
        payload = {
            "production_scene_plan": request.scene_plan.model_dump(mode="json"),
            "visual_asset_planning_configuration": request.configuration.model_dump(mode="json"),
        }
        user = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(user.encode("utf-8")) > self._max_scene_plan_bytes:
            raise ValueError("scene plan exceeds visual asset prompt limit")
        system = (
            "Transform only the supplied approved ProductionScenePlan into visual asset "
            "specifications; do not create or download assets. Return exclusively one JSON "
            "object matching production_visual_asset_plan. Preserve title, language, scenes, "
            "shots, camera intent, timing, and deterministic source IDs. Provide at least one "
            "primary visual specification for every shot, keep all references acyclic and "
            "backward-only, and use a typed consistency profile for recurring characters, "
            "locations, props, palette, lighting, and style. Preserve each scene story_beat "
            "as scene-specific visual intent while keeping the global narrative arc separate "
            "from visual identity. Do not add narration, change "
            "scenes, invent real people, emit markdown, executable content, URLs for downloads, "
            "paths, secrets, headers, binary data, data URLs, or provider configuration. Set "
            "source_scene_plan_artifact_id and source_scene_plan_sha256 to null; runtime adds "
            "durable provenance after validation."
        )
        schema = PlanningPromptBuilder._strict_schema(ProductionVisualAssetPlan.model_json_schema())
        if not isinstance(schema, dict):
            raise TypeError("production visual asset plan schema must be an object")
        return VisualAssetPlanningPrompt(
            version=self.visual_asset_planning_prompt_version,
            system=system,
            user=user,
            response_schema=cast(dict[str, object], schema),
        )
