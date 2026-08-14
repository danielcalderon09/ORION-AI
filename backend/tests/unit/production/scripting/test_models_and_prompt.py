"""ProductionScript, configuration, serialization, and prompt contracts."""

import json

import pytest
from pydantic import ValidationError

from backend.src.production.scripting.configuration import ScriptingConfiguration
from backend.src.production.scripting.duration_policy import (
    PUNCTUATION_ALLOWANCE_MS,
    narration_prompt_word_count_bounds,
    narration_scene_word_budgets,
)
from backend.src.production.scripting.models import (
    ProductionScript,
    ProductionScriptScene,
    validate_script_against_plan,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.src.production.scripting.serialization import serialize_production_script


@pytest.mark.asyncio
async def test_valid_script_is_canonical_and_maps_every_plan_scene(scripting_request) -> None:
    script = (await SimulatedScriptingProvider().generate_script(scripting_request)).script
    assert validate_script_against_plan(script, scripting_request.plan) is script
    assert [scene.scene_number for scene in script.scenes] == [1, 2]
    assert [scene.source_scene_number for scene in script.scenes] == [1, 2]
    assert serialize_production_script(script) == serialize_production_script(script)
    assert json.loads(serialize_production_script(script))["language"] == "en"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("narration", ""),
        ("narration", "<script>alert(1)</script>"),
        ("visual_intent", "../../private"),
        ("delivery_style", "powershell -Command whoami"),
        ("heading", "C:\\private\\script.txt"),
    ],
)
async def test_scene_rejects_empty_or_executable_content(scripting_request, field, value) -> None:
    script = (await SimulatedScriptingProvider().generate_script(scripting_request)).script
    payload = script.scenes[0].model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        ProductionScriptScene.model_validate(payload)


@pytest.mark.asyncio
async def test_script_rejects_extra_duplicate_missing_and_wrong_duration(
    scripting_request,
) -> None:
    script = (await SimulatedScriptingProvider().generate_script(scripting_request)).script
    with pytest.raises(ValidationError):
        ProductionScript.model_validate({**script.model_dump(), "unexpected": True})
    duplicate = script.model_dump()
    duplicate["scenes"][1]["source_scene_number"] = 1
    with pytest.raises(ValidationError):
        ProductionScript.model_validate(duplicate)
    missing = script.model_copy(update={"scenes": script.scenes[:1]})
    with pytest.raises(ValueError, match="every plan scene"):
        validate_script_against_plan(missing, scripting_request.plan)
    wrong_language = script.model_copy(update={"language": "es"})
    with pytest.raises(ValueError, match="language"):
        validate_script_against_plan(wrong_language, scripting_request.plan)


def test_configuration_forbids_private_or_unknown_options() -> None:
    assert ScriptingConfiguration().narration_density == "balanced"
    with pytest.raises(ValidationError):
        ScriptingConfiguration.model_validate({"provider": "openai"})
    with pytest.raises(ValidationError):
        ScriptingConfiguration.model_validate({"tone": "bash -c whoami"})


def test_prompt_is_deterministic_strict_and_excludes_internal_metadata(
    scripting_request,
) -> None:
    builder = ScriptingPromptBuilder(max_plan_bytes=100_000)
    first = builder.build(scripting_request)
    assert first == builder.build(scripting_request)
    assert first.version == "2.5.0"
    assert "Every scene must add new information" in first.system
    assert "omit a call to action" in first.system
    assert first.response_schema["additionalProperties"] is False
    user_payload = json.loads(first.user)
    assert "metadata" not in user_payload["source_plan"]
    assert user_payload["narration_word_count_policy"] == {
        "maximum_total_words": 47,
        "minimum_total_words": 10,
        "maximum_words_per_scene": [23, 24],
        "scene_word_budgets": [
            {"maximum_words": 23, "scene_number": 1},
            {"maximum_words": 24, "scene_number": 2},
        ],
        "scope": "all_scenes_combined",
    }
    assert user_payload["narration_duration_policy"] == {
        "configured_reading_speed_words_per_minute": 150,
        "maximum_estimated_duration_ms": 20_000,
        "post_synthesis_tolerance_is_writing_budget": False,
        "prefer_concise_sentences": True,
        "prompt_headroom_reserves_one_punctuation_per_words": 5,
        "punctuation_adds_estimated_duration": True,
        "punctuation_allowance_ms_per_mark": 120,
        "scope": "all_scenes_combined",
        "semantic_requirement": (
            "The deterministic estimated speaking duration of all narration must not exceed "
            "the requested duration."
        ),
    }
    with pytest.raises(ValueError, match="prompt limit"):
        ScriptingPromptBuilder(max_plan_bytes=10).build(scripting_request)


def test_four_second_prompt_exposes_conservative_short_narration_bound(
    scripting_request,
) -> None:
    source_scene = scripting_request.plan.scenes[0]
    short_plan = scripting_request.plan.model_copy(
        update={
            "target_duration_seconds": 4,
            "scenes": (
                source_scene.model_copy(update={"estimated_duration_seconds": 4}),
            ),
        }
    )
    short_request = scripting_request.model_copy(
        update={"plan": short_plan, "target_duration_seconds": 4}
    )

    prompt = ScriptingPromptBuilder(max_plan_bytes=100_000).build(short_request)

    assert json.loads(prompt.user)["narration_word_count_policy"] == {
        "maximum_total_words": 9,
        "minimum_total_words": 2,
        "maximum_words_per_scene": [9],
        "scene_word_budgets": [{"maximum_words": 9, "scene_number": 1}],
        "scope": "all_scenes_combined",
    }


def test_story_aware_word_budgets_scale_and_remain_within_global_limit() -> None:
    short = narration_scene_word_budgets(
        target_duration_seconds=8,
        scene_count=2,
        reading_speed_words_per_minute=150,
    )
    long_form = narration_scene_word_budgets(
        target_duration_seconds=45,
        scene_count=5,
        reading_speed_words_per_minute=150,
    )
    assert short == (9, 9)
    assert long_form == (19, 21, 23, 22, 20)
    assert sum(short) == 18
    assert sum(long_form) == 105
    assert narration_scene_word_budgets(
        target_duration_seconds=45,
        scene_count=5,
        reading_speed_words_per_minute=150,
    ) == long_form


def test_25_second_prompt_budget_reserves_deterministic_punctuation_headroom() -> None:
    minimum, maximum = narration_prompt_word_count_bounds(
        target_duration_seconds=25,
        scene_count=2,
        reading_speed_words_per_minute=150,
    )
    scene_budgets = narration_scene_word_budgets(
        target_duration_seconds=25,
        scene_count=2,
        reading_speed_words_per_minute=150,
    )

    assert (minimum, maximum) == (12, 58)
    assert scene_budgets == (28, 30)
    assert sum(scene_budgets) == maximum
    reserved_punctuation = 12
    assert maximum * 400 + reserved_punctuation * PUNCTUATION_ALLOWANCE_MS <= 25_000


@pytest.mark.parametrize("scene_count", [1, 2, 3, 5])
def test_conservative_scene_budgets_are_deterministic_and_globally_bounded(
    scene_count: int,
) -> None:
    bounds = narration_prompt_word_count_bounds(
        target_duration_seconds=25,
        scene_count=scene_count,
        reading_speed_words_per_minute=150,
    )
    first = narration_scene_word_budgets(
        target_duration_seconds=25,
        scene_count=scene_count,
        reading_speed_words_per_minute=150,
    )

    assert first == narration_scene_word_budgets(
        target_duration_seconds=25,
        scene_count=scene_count,
        reading_speed_words_per_minute=150,
    )
    assert sum(first) <= bounds[1]
