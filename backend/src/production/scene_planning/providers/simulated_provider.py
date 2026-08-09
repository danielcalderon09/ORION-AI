"""Deterministic no-network scene-planning provider."""

from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScene,
    ProductionScenePlan,
    ProductionShot,
    ProductionTiming,
    ProductionTransition,
    validate_scene_plan_against_script,
)
from backend.src.production.scene_planning.ports import ScenePlanningProviderResponse
from backend.src.production.scripting.models import ProductionScript


class SimulatedScenePlanningProvider:
    async def generate_scene_plan(
        self,
        script: ProductionScript,
    ) -> ScenePlanningProviderResponse:
        last_scene = len(script.scenes)
        scenes = tuple(
            ProductionScene(
                scene_id=f"scene-{index:03d}",
                scene_number=index,
                source_scene_number=source.scene_number,
                title=source.heading,
                narration=source.narration,
                objective=source.visual_intent,
                story_beat=source.story_beat,
                estimated_duration_seconds=source.estimated_duration_seconds,
                shots=(
                    ProductionShot(
                        shot_id=f"scene-{index:03d}-shot-001",
                        shot_number=1,
                        scene_number=index,
                        objective=source.visual_intent,
                        description=source.visual_intent,
                        camera=ProductionCamera(
                            framing="medium",
                            angle="eye_level",
                            movement="static",
                            lens_millimeters=50,
                            subject=source.visual_intent,
                        ),
                        timing=ProductionTiming(
                            start_seconds=0,
                            duration_seconds=source.estimated_duration_seconds,
                            end_seconds=source.estimated_duration_seconds,
                        ),
                        transition=ProductionTransition(
                            kind="none" if index == last_scene else "cut",
                            duration_seconds=0,
                        ),
                    ),
                ),
            )
            for index, source in enumerate(script.scenes, start=1)
        )
        plan = ProductionScenePlan(
            source_script_schema_version=script.schema_version,
            title=script.title,
            language=script.language,
            target_duration_seconds=script.target_duration_seconds,
            scenes=scenes,
        )
        validate_scene_plan_against_script(plan, script)
        return ScenePlanningProviderResponse(
            scene_plan=plan,
            provider="orion-simulated",
            model="scene-planning-simulator-v1",
            requested_model="scene-planning-simulator-v1",
            reported_model="scene-planning-simulator-v1",
            latency_ms=0,
            finish_reason="simulated",
        )

    async def close(self) -> None:
        return None
