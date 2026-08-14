"""Pure, versioned construction of scripting Structured Output prompts."""

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.scripting.duration_policy import (
    PROMPT_WORDS_PER_PUNCTUATION,
    PUNCTUATION_ALLOWANCE_MS,
    narration_prompt_word_count_bounds,
    narration_scene_word_budgets,
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
    estimated_duration_ms: int
    target_duration_ms: int
    excess_duration_ms: int
    required_reduction_ratio: str
    current_word_count: int
    narrative_context: NarrativeRetryContext


class ScriptingPromptBuilder:
    scripting_prompt_version = "2.5.0"
    structured_output_mode = "json_schema"
    system_instruction = (
        "Create a production-ready voice-over script from the supplied durable production "
        "plan. Return only one JSON object matching production_script; do not use Markdown "
        "or provider commentary. Write in the requested language and preserve one ordered "
        "script scene per source scene, including source_scene_number and exact planned "
        "durations. Narration must be clear, non-empty, subtitle-compatible, naturally paced, "
        "and suitable for voice-over. Keep the combined narration across all scenes within the "
        "requested deterministic narration_duration_policy; the post-synthesis tolerance is "
        "reserved for voice variation and is not a writing budget. Treat per-scene word budgets "
        "as guidance, not mandatory fill targets. Every scene must add "
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
        minimum_words, maximum_words = narration_prompt_word_count_bounds(
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
        if retry_context is not None:
            maximum_words = retry_context.maximum_total_words
            scene_word_budgets = retry_context.scene_word_budgets
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
            "narration_duration_policy": {
                "configured_reading_speed_words_per_minute": (
                    request.configuration.reading_speed_words_per_minute
                ),
                "maximum_estimated_duration_ms": round(
                    request.target_duration_seconds * 1_000
                ),
                "post_synthesis_tolerance_is_writing_budget": False,
                "punctuation_adds_estimated_duration": True,
                "punctuation_allowance_ms_per_mark": PUNCTUATION_ALLOWANCE_MS,
                "prompt_headroom_reserves_one_punctuation_per_words": (
                    PROMPT_WORDS_PER_PUNCTUATION
                ),
                "prefer_concise_sentences": True,
                "scope": "all_scenes_combined",
                "semantic_requirement": (
                    "The deterministic estimated speaking duration of all narration must not "
                    "exceed the requested duration."
                ),
            },
            "target_duration_seconds": request.target_duration_seconds,
        }
        if retry_context is not None:
            user_payload["duration_policy_retry"] = {
                "attempt": retry_context.retry_number,
                "previous_output_exceeded_budget": True,
                "maximum_total_words": retry_context.maximum_total_words,
                "maximum_words_per_scene": retry_context.scene_word_budgets,
                "estimated_duration_ms": retry_context.estimated_duration_ms,
                "target_duration_ms": retry_context.target_duration_ms,
                "excess_duration_ms": retry_context.excess_duration_ms,
                "required_proportional_reduction": (
                    retry_context.required_reduction_ratio
                ),
                "current_word_count": retry_context.current_word_count,
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
