"""Pure, versioned construction of provider-neutral planning prompts."""

import json
from typing import cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.planning.ports import PlanningProviderRequest


class PlanningPrompt(ContractModel):
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    system: str
    user: str
    response_schema: dict[str, object]


class PlanningPromptBuilder:
    planning_prompt_version = "1.0.0"
    _unsupported_strict_keywords = frozenset(
        {
            "default",
            "format",
            "maxItems",
            "maxLength",
            "maximum",
            "minItems",
            "minLength",
            "minimum",
            "pattern",
        }
    )

    def build(self, request: PlanningProviderRequest) -> PlanningPrompt:
        configuration = request.configuration.model_dump(mode="json")
        user_payload = {
            "schema_version": "1.0.0",
            "original_prompt": request.prompt,
            "configuration": configuration,
            "target_duration_seconds": request.target_duration_seconds,
            "language": request.language,
            "aspect_ratio": request.aspect_ratio,
        }
        system = (
            "You plan safe, coherent videos. Return exclusively one JSON object matching "
            "the supplied production_plan schema. Do not emit markdown, HTML, shell "
            "commands, local paths, credentials, or commentary outside JSON. Scene numbers "
            "must start at 1 and be consecutive; scene durations must sum exactly to the "
            "target duration."
        )
        response_schema = self._strict_schema(ProductionPlan.model_json_schema())
        if not isinstance(response_schema, dict):
            raise TypeError("production plan schema must be an object")
        return PlanningPrompt(
            version=self.planning_prompt_version,
            system=system,
            user=json.dumps(
                user_payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            response_schema=cast(dict[str, object], response_schema),
        )

    @classmethod
    def _strict_schema(cls, value: object) -> object:
        if isinstance(value, dict):
            result = {
                str(key): cls._strict_schema(child)
                for key, child in value.items()
                if key not in cls._unsupported_strict_keywords
            }
            properties = result.get("properties")
            if isinstance(properties, dict):
                result["required"] = list(properties)
                result["additionalProperties"] = False
            return result
        if isinstance(value, list):
            return [cls._strict_schema(child) for child in value]
        return value
