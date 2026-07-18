"""Deterministic no-network PlanningProvider used by default."""

from backend.src.production.planning.models import ProductionPlan, ProductionScenePlan
from backend.src.production.planning.ports import (
    PlanningProviderRequest,
    PlanningProviderResponse,
)


class SimulatedPlanningProvider:
    async def generate_plan(
        self,
        request: PlanningProviderRequest,
    ) -> PlanningProviderResponse:
        config = request.configuration
        count = config.scene_count_hint
        portion = request.target_duration_seconds / count
        durations = [portion for _ in range(count)]
        durations[-1] = request.target_duration_seconds - sum(durations[:-1])
        topic = " ".join(request.prompt.split())
        short_topic = topic[:120]
        scenes = tuple(
            ProductionScenePlan(
                scene_number=index,
                title=f"Scene {index}: {short_topic[:80]}",
                narration=f"Narration for scene {index} about {short_topic}.",
                visual_description=(
                    f"A {config.visual_style} visual illustrating scene {index} of {short_topic}."
                ),
                image_prompt=(
                    f"{config.visual_style}, scene {index}, {short_topic}, "
                    f"aspect ratio {request.aspect_ratio}"
                ),
                motion_instruction="Slow controlled camera movement",
                estimated_duration_seconds=duration,
                transition="cut" if index < count else "fade_to_black",
                on_screen_text=short_topic if index == 1 else None,
                metadata={"simulated": True},
            )
            for index, duration in enumerate(durations, start=1)
        )
        plan = ProductionPlan(
            title=short_topic[:100],
            summary=f"A structured video plan about {short_topic}.",
            language=request.language,
            target_duration_seconds=request.target_duration_seconds,
            aspect_ratio=request.aspect_ratio,
            visual_style=config.visual_style,
            narrative_style=config.narrative_style,
            scenes=scenes,
            metadata={"simulated": True},
        )
        return PlanningProviderResponse(
            plan=plan,
            provider="orion-simulated",
            model="planning-simulator-v1",
            latency_ms=0,
            finish_reason="simulated",
            metadata={"deterministic": True, "simulated": True},
        )

    async def close(self) -> None:
        return None
