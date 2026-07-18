"""Planning model, safety, serialization, and prompt builder tests."""

import json

import pytest
from pydantic import ValidationError

from backend.src.production.planning.models import ProductionPlan, ProductionScenePlan
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.serialization import serialize_production_plan


def scene(number: int, duration: float = 10) -> ProductionScenePlan:
    return ProductionScenePlan(
        scene_number=number,
        title=f"Scene {number}",
        narration="Safe narration",
        visual_description="Safe visual",
        image_prompt="A cinematic safe image",
        motion_instruction="Slow zoom",
        estimated_duration_seconds=duration,
        transition="cut",
    )


def test_plan_is_strict_immutable_and_canonical() -> None:
    plan = ProductionPlan(
        title="Eclipse",
        summary="A short explanation",
        language="en",
        target_duration_seconds=20,
        aspect_ratio="9:16",
        visual_style="cinematic",
        narrative_style="educational",
        scenes=(scene(1), scene(2)),
    )
    assert json.loads(serialize_production_plan(plan))["title"] == "Eclipse"
    with pytest.raises(ValidationError):
        plan.title = "Changed"
    with pytest.raises(ValidationError):
        ProductionPlan(**plan.model_dump(), unknown=True)


@pytest.mark.parametrize(
    ("scenes", "duration"),
    [((scene(2),), 10), ((scene(1), scene(2)), 30)],
)
def test_plan_rejects_scene_order_and_duration(scenes, duration) -> None:
    with pytest.raises(ValidationError):
        ProductionPlan(
            title="Invalid",
            summary="Invalid plan",
            language="en",
            target_duration_seconds=duration,
            aspect_ratio="9:16",
            visual_style="clean",
            narrative_style="clear",
            scenes=scenes,
        )


@pytest.mark.parametrize(
    "unsafe",
    ["<script>alert(1)</script>", "powershell -c bad", "../../outside", "C:\\private\\x"],
)
def test_plan_rejects_unsafe_text(unsafe: str) -> None:
    with pytest.raises(ValidationError):
        scene(1).model_copy(update={"image_prompt": unsafe}).model_validate(
            {**scene(1).model_dump(), "image_prompt": unsafe}
        )


def test_prompt_builder_is_deterministic_and_contains_no_secrets(planning_request) -> None:
    builder = PlanningPromptBuilder()
    first = builder.build(planning_request)
    second = builder.build(planning_request)
    assert first == second
    assert first.version == "1.0.0"
    assert planning_request.prompt in first.user
    assert "api_key" not in first.user.lower()
    assert first.response_schema["additionalProperties"] is False
    assert "maxLength" not in json.dumps(first.response_schema)
