"""Pure, versioned construction of scripting Structured Output prompts."""

import json
from typing import cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.ports import ScriptingProviderRequest


class ScriptingPrompt(ContractModel):
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    system: str
    user: str
    response_schema: dict[str, object]


class ScriptingPromptBuilder:
    scripting_prompt_version = "1.0.0"

    def __init__(self, *, max_plan_bytes: int) -> None:
        if max_plan_bytes < 1:
            raise ValueError("maximum prompt plan size must be positive")
        self._max_plan_bytes = max_plan_bytes

    def build(self, request: ScriptingProviderRequest) -> ScriptingPrompt:
        plan_payload = request.plan.model_dump(mode="json", exclude={"metadata"})
        user_payload = {
            "schema_version": "1.0.0",
            "source_plan": plan_payload,
            "configuration": request.configuration.model_dump(mode="json"),
            "language": request.language,
            "target_duration_seconds": request.target_duration_seconds,
        }
        user = json.dumps(
            user_payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(user.encode("utf-8")) > self._max_plan_bytes:
            raise ValueError("production plan exceeds scripting prompt limit")
        system = (
            "Write a production-ready narration script from the supplied production plan. "
            "Return exclusively one JSON object matching production_script. Preserve one "
            "ordered script scene per source scene and its source_scene_number, language, "
            "and exact scene durations. Narration must be useful and non-empty. Do not emit "
            "markdown, executable HTML, shell commands, filesystem instructions, paths, "
            "credentials, or commentary outside JSON."
        )
        schema = PlanningPromptBuilder._strict_schema(ProductionScript.model_json_schema())
        if not isinstance(schema, dict):
            raise TypeError("production script schema must be an object")
        return ScriptingPrompt(
            version=self.scripting_prompt_version,
            system=system,
            user=user,
            response_schema=cast(dict[str, object], schema),
        )
