"""Deterministic no-network ScriptingProvider used by default."""

from backend.src.production.scripting.duration_policy import (
    allocate_narration_scene_word_budgets,
    narration_prompt_word_count_bounds,
)
from backend.src.production.scripting.models import (
    ProductionScript,
    ProductionScriptScene,
    ensure_narrative_progression,
    validate_script_against_plan,
)
from backend.src.production.scripting.ports import (
    ScriptingProviderRequest,
    ScriptingProviderResponse,
)


class SimulatedScriptingProvider:
    async def generate_script(
        self,
        request: ScriptingProviderRequest,
    ) -> ScriptingProviderResponse:
        configuration = request.configuration
        _, maximum_total_words = narration_prompt_word_count_bounds(
            target_duration_seconds=request.target_duration_seconds,
            scene_count=len(request.plan.scenes),
            reading_speed_words_per_minute=(
                configuration.reading_speed_words_per_minute
            ),
        )
        scene_word_budgets = allocate_narration_scene_word_budgets(
            scene_count=len(request.plan.scenes),
            maximum_total_words=maximum_total_words,
        )
        scenes = tuple(
            ProductionScriptScene(
                scene_number=index,
                source_scene_number=source.scene_number,
                heading=source.title,
                narration=self._narration(
                    source.narration,
                    min(configuration.max_words_per_scene, scene_word_budgets[index - 1]),
                ),
                estimated_duration_seconds=source.estimated_duration_seconds,
                delivery_style=f"{configuration.tone}; {configuration.narration_density}",
                pronunciation_notes=(),
                on_screen_text=(
                    source.on_screen_text if configuration.preserve_on_screen_text else None
                ),
                visual_intent=source.visual_description,
                transition_note=source.transition,
                metadata={"simulated": True},
            )
            for index, source in enumerate(request.plan.scenes, start=1)
        )
        script = ProductionScript(
            source_plan_schema_version=request.plan.schema_version,
            title=request.plan.title,
            language=request.language,
            target_duration_seconds=request.target_duration_seconds,
            tone=configuration.tone,
            opening_hook=(
                request.plan.summary
                if configuration.include_opening_hook
                else f"Introduction to {request.plan.title}"
            ),
            closing_call_to_action=(
                f"Continue exploring {request.plan.title}."
                if configuration.include_call_to_action
                else None
            ),
            scenes=scenes,
            metadata={"simulated": True},
        )
        script = ensure_narrative_progression(script)
        validate_script_against_plan(script, request.plan)
        return ScriptingProviderResponse(
            script=script,
            provider="orion-simulated",
            model="scripting-simulator-v1",
            requested_model="scripting-simulator-v1",
            reported_model="scripting-simulator-v1",
            latency_ms=0,
            finish_reason="simulated",
            metadata={"deterministic": True, "simulated": True},
        )

    @staticmethod
    def _narration(value: str, max_words: int) -> str:
        words = value.split()
        useful_context = ["This", "explanation", "adds", "concrete", "context", "and", "useful", "detail", "while", "advancing", "the", "narrative", "clearly", "for", "the", "audience"]
        while len(words) < max_words:
            words.extend(useful_context[: max_words - len(words)])
        return " ".join(words[:max_words])

    async def close(self) -> None:
        return None
