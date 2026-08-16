"""Pure, versioned construction of scripting Structured Output prompts."""

import hashlib
import json
from typing import Literal, cast

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.duration_resolution import NarrationOccupancyPolicy
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.scripting.duration_policy import (
    PROMPT_WORDS_PER_PUNCTUATION,
    PUNCTUATION_ALLOWANCE_MS,
    narration_prompt_word_count_bounds,
    narration_scene_word_budgets,
)
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.narration_compression import (
    NarrationCompressionRequest,
    NarrationCompressionResponse,
)
from backend.src.production.scripting.narration_expansion import (
    NarrationExpansionRequest,
    NarrationExpansionResponse,
)
from backend.src.production.scripting.ports import ScriptingProviderRequest


class ScriptingPrompt(ContractModel):
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    system: str
    user: str
    response_schema: dict[str, object]


class ScriptingPromptBuilder:
    scripting_prompt_version = "2.9.0"
    structured_output_mode = "json_schema"
    system_instruction = (
        "Create a production-ready voice-over script from the supplied durable production "
        "plan. Return only one JSON object matching production_script; do not use Markdown "
        "or provider commentary. Write in the requested language and preserve one ordered "
        "script scene per source scene, including source_scene_number and exact planned "
        "durations. Narration must be clear, non-empty, subtitle-compatible, naturally paced, "
        "and suitable for voice-over. Keep the combined narration across all scenes within the "
        "supplied maximum_total_words hard limit and the requested deterministic "
        "narration_duration_policy; the post-synthesis tolerance is "
        "reserved for voice variation and is not a writing budget. Treat per-scene word budgets "
        "as guidance, not mandatory fill targets. Aim for the supplied ideal estimated duration "
        "and do not fall below its minimum occupancy. Every scene must add "
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
    compression_system_instruction = (
        "Compress only the supplied scene narrations. Return one JSON object matching "
        "narration_compression; do not return a ProductionScript, Markdown, explanations, or "
        "fields outside the schema. Preserve every source_scene_number exactly once and keep "
        "the original meaning, essential narrative beats, facts, language, and order. Remove "
        "redundancy, unnecessary adjectives, repeated explanations, and filler without "
        "introducing new facts. Do not make the narration as short as possible or remove useful "
        "information merely to minimize word count. The combined narration MUST remain between "
        "minimum_total_words and maximum_total_words; both are hard limits. Each scene narration "
        "MUST remain between its minimum_words and maximum_words limits. Compress only enough to "
        "enter the supplied duration band and aim as close as practical to ideal_duration_ms."
    )
    expansion_system_instruction = (
        "Expand only the supplied scene narrations. Return one JSON object matching "
        "narration_expansion; do not return a ProductionScript, Markdown, explanations, or "
        "fields outside the schema. Return exactly the requested scenes: do not add scenes, "
        "remove scenes, change scene numbers, or change the requested language. Preserve every "
        "source_scene_number exactly once and keep the original facts, language, meaning, style, "
        "intent, and order. Add only useful "
        "explanatory detail supported by the supplied semantic constraints. Do not repeat "
        "phrases, invent facts, or add empty filler. Each scene narration MUST NOT exceed its "
        "specified maximum_words hard limit. The combined narration MUST NOT exceed the "
        "maximum_total_words hard limit and should approach ideal_duration_ms. Return only "
        "expanded narration for the requested scenes."
    )

    def __init__(self, *, max_plan_bytes: int) -> None:
        if max_plan_bytes < 1:
            raise ValueError("maximum prompt plan size must be positive")
        self._max_plan_bytes = max_plan_bytes

    def build(
        self,
        request: ScriptingProviderRequest,
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
        occupancy_policy = NarrationOccupancyPolicy()
        target_duration_ms = round(request.target_duration_seconds * 1_000)
        user_payload = {
            "schema_version": "1.0.0",
            "source_plan": plan_payload,
            "configuration": request.configuration.model_dump(mode="json"),
            "language": request.language,
            "narration_word_count_policy": {
                "hard_limit_instruction": (
                    f"The combined narration MUST NOT exceed {maximum_words} total words. "
                    "The total word count is a hard limit."
                ),
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
                "minimum_target_occupancy_ratio": str(
                    occupancy_policy.minimum_occupancy_ratio
                ),
                "ideal_target_occupancy_ratio": str(
                    occupancy_policy.ideal_occupancy_ratio
                ),
                "minimum_estimated_duration_ms": occupancy_policy.duration_for_ratio(
                    target_duration_ms,
                    occupancy_policy.minimum_occupancy_ratio,
                ),
                "ideal_estimated_duration_ms": occupancy_policy.duration_for_ratio(
                    target_duration_ms,
                    occupancy_policy.ideal_occupancy_ratio,
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

    def build_compression(self, request: NarrationCompressionRequest) -> ScriptingPrompt:
        """Build the narrow, hard-capped narration-only compression prompt."""

        payload = request.model_dump(mode="json", exclude={"job_id"})
        payload["hard_limit_instruction"] = (
            f"Your revised narration MUST contain between {request.minimum_total_words} and "
            f"{request.maximum_total_words} total words. Both bounds are mandatory hard limits. "
            f"Keep its deterministic duration between {request.minimum_duration_ms} ms and "
            f"{request.maximum_duration_ms} ms, aiming for {request.ideal_duration_ms} ms."
        )
        payload["output_constraints"] = (
            "Do not add explanations.",
            "Do not introduce new facts.",
            "Preserve meaning and order while removing unnecessary wording.",
            "Do not remove useful information merely to minimize word count.",
        )
        user = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(user.encode("utf-8")) > self._max_plan_bytes:
            raise ValueError("narration compression input exceeds prompt limit")
        return ScriptingPrompt(
            version=self.scripting_prompt_version,
            system=self.compression_system_instruction,
            user=user,
            response_schema=self._compression_response_schema(),
        )

    def build_expansion(self, request: NarrationExpansionRequest) -> ScriptingPrompt:
        """Build the narrow, bounded narration-only expansion prompt."""

        payload = request.model_dump(mode="json", exclude={"job_id"})
        payload["hard_limit_instruction"] = (
            f"Your output narration MUST NOT exceed {request.maximum_total_words} total words. "
            f"Aim for approximately {request.ideal_duration_ms} ms and never fall below "
            f"{request.minimum_duration_ms} ms under the supplied speaking-rate model."
        )
        payload["output_constraints"] = (
            "Do not add explanations outside scene narration.",
            "Do not introduce facts absent from the semantic constraints.",
            "Do not repeat phrases or use empty filler.",
        )
        user = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(user.encode("utf-8")) > self._max_plan_bytes:
            raise ValueError("narration expansion input exceeds prompt limit")
        return ScriptingPrompt(
            version=self.scripting_prompt_version,
            system=self.expansion_system_instruction,
            user=user,
            response_schema=self._expansion_response_schema(request),
        )

    @classmethod
    def template_fingerprint(
        cls,
        request_purpose: Literal[
            "production_script", "narration_compression", "narration_expansion"
        ] = (
            "production_script"
        ),
    ) -> str:
        compression = request_purpose == "narration_compression"
        expansion = request_purpose == "narration_expansion"
        payload = {
            "version": cls.scripting_prompt_version,
            "structured_output_mode": cls.structured_output_mode,
            "request_purpose": request_purpose,
            "system_instruction": (
                cls.compression_system_instruction
                if compression
                else cls.expansion_system_instruction
                if expansion
                else cls.system_instruction
            ),
            "response_schema": (
                cls._compression_response_schema()
                if compression
                else cls._expansion_response_schema()
                if expansion
                else cls._response_schema()
            ),
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

    @staticmethod
    def _compression_response_schema() -> dict[str, object]:
        schema = PlanningPromptBuilder._strict_schema(
            NarrationCompressionResponse.model_json_schema()
        )
        if not isinstance(schema, dict):
            raise TypeError("narration compression schema must be an object")
        return cast(dict[str, object], schema)

    @staticmethod
    def _expansion_response_schema(
        request: NarrationExpansionRequest | None = None,
    ) -> dict[str, object]:
        schema = PlanningPromptBuilder._strict_schema(
            NarrationExpansionResponse.model_json_schema()
        )
        if not isinstance(schema, dict):
            raise TypeError("narration expansion schema must be an object")
        if request is not None:
            properties = schema.get("properties")
            definitions = schema.get("$defs")
            if not isinstance(properties, dict) or not isinstance(definitions, dict):
                raise TypeError("narration expansion schema structure is invalid")
            language = properties.get("language")
            scenes = properties.get("scenes")
            schema_version = properties.get("schema_version")
            scene_definition = definitions.get("NarrationExpansionScene")
            if (
                not isinstance(language, dict)
                or not isinstance(scenes, dict)
                or not isinstance(schema_version, dict)
                or not isinstance(scene_definition, dict)
            ):
                raise TypeError("narration expansion schema properties are invalid")
            scene_properties = scene_definition.get("properties")
            if not isinstance(scene_properties, dict):
                raise TypeError("narration expansion scene schema is invalid")
            source_scene_number = scene_properties.get("source_scene_number")
            if not isinstance(source_scene_number, dict):
                raise TypeError("narration expansion scene identity schema is invalid")
            expected_scene_numbers = [
                scene.source_scene_number for scene in request.scenes
            ]
            schema_version["enum"] = ["1.0.0"]
            language["enum"] = [request.language]
            source_scene_number["enum"] = expected_scene_numbers
            scenes["minItems"] = len(expected_scene_numbers)
            scenes["maxItems"] = len(expected_scene_numbers)
        return cast(dict[str, object], schema)
