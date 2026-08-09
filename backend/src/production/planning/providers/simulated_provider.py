"""Deterministic no-network PlanningProvider used by default."""

from backend.src.production.planning.duration_allocation import (
    SceneDurationInput,
    allocate_scene_durations,
)
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
        topic = " ".join(request.prompt.split())
        short_topic = topic[:120]
        narrations = tuple(
            f"Narration for scene {index} about {short_topic}." for index in range(1, count + 1)
        )
        allocations = allocate_scene_durations(
            target_duration_ms=round(request.target_duration_seconds * 1_000),
            scenes=tuple(
                SceneDurationInput(
                    scene_id=f"scene-{index:03d}",
                    scene_number=index,
                    narration_word_count=len(narrations[index - 1].split()),
                )
                for index in range(1, count + 1)
            ),
        )
        scenes = tuple(
            ProductionScenePlan(
                scene_number=index,
                title=f"Scene {index}: {short_topic[:80]}",
                narration=narrations[index - 1],
                visual_description=(
                    f"A {config.visual_style} visual illustrating scene {index} of {short_topic}."
                ),
                image_prompt=(
                    f"{config.visual_style}, scene {index}, {short_topic}, "
                    f"aspect ratio {request.aspect_ratio}"
                ),
                motion_instruction="Slow controlled camera movement",
                estimated_duration_seconds=allocation.planned_duration_ms / 1_000,
                transition="cut" if index < count else "fade_to_black",
                on_screen_text=short_topic if index == 1 else None,
                metadata={"simulated": True},
            )
            for index, allocation in enumerate(allocations, start=1)
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
            requested_model="planning-simulator-v1",
            reported_model="planning-simulator-v1",
            latency_ms=0,
            finish_reason="simulated",
            metadata={"deterministic": True, "simulated": True},
        )

    async def close(self) -> None:
        return None
