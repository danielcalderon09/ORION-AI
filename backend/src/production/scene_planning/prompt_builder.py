"""Pure Structured Output prompt construction from ProductionScript only."""

import json
from typing import cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.scripting.models import ProductionScript


class ScenePlanningPrompt(ContractModel):
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    system: str
    user: str
    response_schema: dict[str, object]


class ScenePlanningPromptBuilder:
    scene_planning_prompt_version = "1.0.0"

    def __init__(self, *, max_script_bytes: int) -> None:
        if max_script_bytes < 1:
            raise ValueError("maximum prompt script size must be positive")
        self._max_script_bytes = max_script_bytes

    def build(self, script: ProductionScript) -> ScenePlanningPrompt:
        script_payload = script.model_dump(mode="json")
        script_payload.pop("metadata", None)
        scenes = script_payload.get("scenes")
        if isinstance(scenes, list):
            for scene in scenes:
                if isinstance(scene, dict):
                    scene.pop("metadata", None)
        user = json.dumps(
            {"production_script": script_payload},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(user.encode("utf-8")) > self._max_script_bytes:
            raise ValueError("production script exceeds scene-planning prompt limit")
        system = (
            "Transform only the supplied approved production script into a production scene "
            "plan. Return exclusively one JSON object matching production_scene_plan. Preserve "
            "the title, language, narration, scene order, source mapping, and exact durations. "
            "Preserve the global narrative_arc and each scene story_beat exactly; scene planning "
            "may express the beat through shots but must not change its narrative role or resolve "
            "the payoff before the final scene. Keep global story context separate from scene-specific "
            "shot intent. "
            "Create ordered, contiguous shots with unique contractual IDs, typed camera choices, "
            "and valid transitions. Set source_script_sha256 to null; runtime supplies integrity "
            "provenance after validation. Do not infer from an original prompt or emit markdown, "
            "executable HTML, commands, filesystem instructions, paths, secrets, or commentary."
        )
        schema = PlanningPromptBuilder._strict_schema(
            ProductionScenePlan.model_json_schema()
        )
        if not isinstance(schema, dict):
            raise TypeError("production scene plan schema must be an object")
        return ScenePlanningPrompt(
            version=self.scene_planning_prompt_version,
            system=system,
            user=user,
            response_schema=cast(dict[str, object], schema),
        )
