"""ProductionScript, configuration, serialization, and prompt contracts."""

import json

import pytest
from pydantic import ValidationError

from backend.src.production.scripting.configuration import ScriptingConfiguration
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
async def test_scene_rejects_empty_or_executable_content(
    scripting_request, field, value
) -> None:
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
    assert first.version == "1.0.0"
    assert first.response_schema["additionalProperties"] is False
    assert "metadata" not in json.loads(first.user)["source_plan"]
    with pytest.raises(ValueError, match="prompt limit"):
        ScriptingPromptBuilder(max_plan_bytes=10).build(scripting_request)
