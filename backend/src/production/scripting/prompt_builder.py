"""Pure, versioned construction of scripting Structured Output prompts."""

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.scripting.duration_policy import (
    narration_scene_word_budgets,
    narration_word_count_bounds,
)
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.ports import ScriptingProviderRequest


class ScriptingPrompt(ContractModel):
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    system: str
    user: str
    response_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class NarrativeRetryContext:
    premise: str
    opening_hook: str
    central_question: str
    progression: tuple[str, ...]
    intended_payoff: str
    ending_state: str
    story_beats: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class DurationPolicyRetryContext:
    retry_number: int
    maximum_total_words: int
    scene_word_budgets: tuple[int, ...]
    narrative_context: NarrativeRetryContext


class ScriptingPromptBuilder:
    scripting_prompt_version = "2.3.0"
    structured_output_mode = "json_schema"
    system_instruction = (
        "Create a production-ready voice-over script from the supplied durable production "
        "plan. Return only one JSON object matching production_script; do not use Markdown "
        "or provider commentary. Write in the requested language and preserve one ordered "
        "script scene per source scene, including source_scene_number and exact planned "
        "durations. Narration must be clear, non-empty, subtitle-compatible, naturally paced, "
        "and suitable for voice-over. Keep the combined narration across all scenes within the "
        "exact narration_word_count_policy supplied by the user payload. Every scene must add "
        "new information while maintaining thematic continuity; do not repeat the introduction. "
        "Populate narrative_arc with the premise, opening hook, central question, progression, "
        "intended payoff, and ending state. Populate each scene story_beat with its adaptive "
        "narrative role, prior context, new information, open question, transition intent, "
        "and explicit repetition constraints. Use hook only at the beginning and payoff or "
        "conclusion only at the end unless the supplied plan explicitly requires otherwise. "
        "For short-form scripts, omit a call to action unless the source plan explicitly requires "
        "one. Do not reference "
        "unavailable assets, imitate copyrighted "
        "characters, present uncertain facts as certain, reveal hidden reasoning, or include "
        "credentials, system details, executable content, shell commands, filesystem paths, "
        "HTML, or fields outside the schema."
    )

    def __init__(self, *, max_plan_bytes: int) -> None:
        if max_plan_bytes < 1:
            raise ValueError("maximum prompt plan size must be positive")
        self._max_plan_bytes = max_plan_bytes

    def build(
        self,
        request: ScriptingProviderRequest,
        *,
        retry_context: DurationPolicyRetryContext | None = None,
    ) -> ScriptingPrompt:
        plan_payload = request.plan.model_dump(mode="json", exclude={"metadata"})
        minimum_words, maximum_words = narration_word_count_bounds(
            target_duration_seconds=request.target_duration_seconds,
            scene_count=len(request.plan.scenes),
            reading_speed_words_per_minute=(
                request.configuration.reading_speed_words_per_minute
            ),
        )
        scene_word_budgets = narration_scene_word_budgets(
            target_duration_seconds=request.target_duration_seconds,
            scene_count=len(request.plan.scenes),
            reading_speed_words_per_minute=(
                request.configuration.reading_speed_words_per_minute
            ),
        )
        user_payload = {
            "schema_version": "1.0.0",
            "source_plan": plan_payload,
            "configuration": request.configuration.model_dump(mode="json"),
            "language": request.language,
            "narration_word_count_policy": {
                "maximum_total_words": maximum_words,
                "minimum_total_words": minimum_words,
                "maximum_words_per_scene": scene_word_budgets,
                "scene_word_budgets": [
                    {
                        "scene_number": index + 1,
                        "maximum_words": budget,
                    }
                    for index, budget in enumerate(scene_word_budgets)
                ],
                "scope": "all_scenes_combined",
            },
            "target_duration_seconds": request.target_duration_seconds,
        }
        if retry_context is not None:
            user_payload["duration_policy_retry"] = {
                "attempt": retry_context.retry_number,
                "previous_output_exceeded_budget": True,
                "maximum_total_words": retry_context.maximum_total_words,
                "maximum_words_per_scene": retry_context.scene_word_budgets,
                "preserve_premise_arc_beats_and_key_facts": True,
                "shorten_narration_without_repeating_or_changing_language": True,
                "narrative_context": {
                    "premise": retry_context.narrative_context.premise,
                    "opening_hook": retry_context.narrative_context.opening_hook,
                    "central_question": retry_context.narrative_context.central_question,
                    "progression": retry_context.narrative_context.progression,
                    "intended_payoff": retry_context.narrative_context.intended_payoff,
                    "ending_state": retry_context.narrative_context.ending_state,
                    "story_beats": retry_context.narrative_context.story_beats,
                },
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
        schema = self._response_schema()
        if not isinstance(schema, dict):
            raise TypeError("production script schema must be an object")
        return ScriptingPrompt(
            version=self.scripting_prompt_version,
            system=self.system_instruction,
            user=user,
            response_schema=schema,
        )

    @classmethod
    def template_fingerprint(cls) -> str:
        payload = {
            "version": cls.scripting_prompt_version,
            "structured_output_mode": cls.structured_output_mode,
            "system_instruction": cls.system_instruction,
            "response_schema": cls._response_schema(),
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _response_schema() -> dict[str, object]:
        schema = PlanningPromptBuilder._strict_schema(ProductionScript.model_json_schema())
        if not isinstance(schema, dict):
            raise TypeError("production script schema must be an object")
        return cast(dict[str, object], schema)
