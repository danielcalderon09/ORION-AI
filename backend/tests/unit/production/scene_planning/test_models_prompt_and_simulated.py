"""Scene-plan schema, validator, prompt, and simulated provider tests."""

import inspect
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.scene_planning.ports import ScenePlanningProvider
from backend.src.production.scene_planning.prompt_builder import ScenePlanningPromptBuilder
from backend.src.production.scene_planning.providers import SimulatedScenePlanningProvider
from backend.src.production.scene_planning.serialization import serialize_scene_plan


@pytest.mark.asyncio
async def test_simulated_provider_is_deterministic_and_preserves_script(
    production_script,
) -> None:
    provider: ScenePlanningProvider = SimulatedScenePlanningProvider()
    first = await provider.generate_scene_plan(production_script)
    second = await provider.generate_scene_plan(production_script)
    assert first == second
    assert first.scene_plan.language == production_script.language
    assert tuple(scene.narration for scene in first.scene_plan.scenes) == tuple(
        scene.narration for scene in production_script.scenes
    )
    assert first.scene_plan.scenes[-1].shots[-1].transition.kind == "none"
    assert first.provider == "orion-simulated"


def test_provider_and_prompt_receive_only_production_script(production_script) -> None:
    parameters = tuple(
        inspect.signature(SimulatedScenePlanningProvider.generate_scene_plan).parameters
    )
    assert parameters == ("self", "script")
    prompt = ScenePlanningPromptBuilder(max_script_bytes=100_000).build(
        production_script
    )
    payload = json.loads(prompt.user)
    assert set(payload) == {"production_script"}
    assert "metadata" not in payload["production_script"]
    assert all("metadata" not in scene for scene in payload["production_script"]["scenes"])
    assert "original prompt" not in prompt.user.lower()


@pytest.mark.asyncio
async def test_schema_rejects_extra_fields_bad_ids_durations_and_transitions(
    production_script,
) -> None:
    plan = (
        await SimulatedScenePlanningProvider().generate_scene_plan(production_script)
    ).scene_plan
    payload = plan.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ProductionScenePlan.model_validate({**payload, "unexpected": True})
    bad_id = deepcopy(payload)
    bad_id["scenes"][0]["scene_id"] = "duplicate"
    with pytest.raises(ValidationError):
        ProductionScenePlan.model_validate(bad_id)
    bad_timing = deepcopy(payload)
    bad_timing["scenes"][0]["shots"][0]["timing"]["end_seconds"] = 9
    with pytest.raises(ValidationError):
        ProductionScenePlan.model_validate(bad_timing)
    bad_transition = deepcopy(payload)
    bad_transition["scenes"][-1]["shots"][-1]["transition"] = {
        "kind": "cut",
        "duration_seconds": 0,
    }
    with pytest.raises(ValidationError, match="final shot"):
        ProductionScenePlan.model_validate(bad_transition)


@pytest.mark.asyncio
async def test_canonical_json_is_stable_utf8_and_has_unique_ids(production_script) -> None:
    plan = (
        await SimulatedScenePlanningProvider().generate_scene_plan(production_script)
    ).scene_plan
    first = serialize_scene_plan(plan)
    assert first == serialize_scene_plan(plan)
    assert "Bogotá" in first.decode("utf-8")
    ids = [scene.scene_id for scene in plan.scenes] + [
        shot.shot_id for scene in plan.scenes for shot in scene.shots
    ]
    assert len(ids) == len(set(ids))
