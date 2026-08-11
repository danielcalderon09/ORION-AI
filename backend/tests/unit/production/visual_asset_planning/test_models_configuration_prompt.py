"""Visual asset contracts, public configuration, security, and prompt tests."""

import json

import pytest
from pydantic import ValidationError

from backend.src.production.visual_asset_planning.configuration import (
    VisualAssetPlanningConfiguration,
    visual_asset_planning_configuration_from_snapshot,
)
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
    validate_visual_asset_plan_against_scene_plan,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProviderRequest,
)
from backend.src.production.visual_asset_planning.prompt_builder import (
    VisualAssetPlanningPromptBuilder,
)
from backend.src.production.visual_asset_planning.providers import (
    SimulatedVisualAssetPlanningProvider,
)
from backend.src.production.visual_asset_planning.serialization import (
    serialize_visual_asset_plan,
)
from backend.tests.unit.production.visual_asset_planning.conftest import (
    COMMAND_ID,
    JOB_ID,
)


async def make_plan(scene_plan, **configuration):
    request = VisualAssetPlanningProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        scene_plan=scene_plan,
        configuration=VisualAssetPlanningConfiguration(**configuration),
    )
    response = await SimulatedVisualAssetPlanningProvider().generate_visual_asset_plan(request)
    return request, response.visual_asset_plan


@pytest.mark.asyncio
async def test_valid_plan_is_frozen_strict_canonical_and_mapped(
    production_scene_plan,
) -> None:
    _, plan = await make_plan(production_scene_plan)
    validate_visual_asset_plan_against_scene_plan(plan, production_scene_plan)
    assert len(plan.assets) == 4
    assert len({asset.asset_id for asset in plan.assets}) == 4
    assert serialize_visual_asset_plan(plan) == serialize_visual_asset_plan(plan)
    with pytest.raises(ValidationError):
        plan.title = "changed"
    with pytest.raises(ValidationError):
        ProductionVisualAssetPlan.model_validate(
            {**plan.model_dump(mode="json"), "unexpected": True}
        )


@pytest.mark.asyncio
async def test_consecutive_shots_require_distinct_visual_intent(
    production_scene_plan,
) -> None:
    _, plan = await make_plan(production_scene_plan)
    first, second = plan.assets[:2]
    assert first.source_scene_id == second.source_scene_id
    assert first.source_shot_id != second.source_shot_id
    assert first.prompt != second.prompt
    changed = plan.model_copy(
        update={
            "assets": (
                first,
                second.model_copy(update={"prompt": first.prompt}),
                *plan.assets[2:],
            )
        }
    )

    with pytest.raises(ValueError, match="distinct visual intent"):
        validate_visual_asset_plan_against_scene_plan(
            changed,
            production_scene_plan,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["assets"].__setitem__(1, data["assets"][0]), "unique"),
        (
            lambda data: data["assets"][0].update({"source_scene_id": "scene-002"}),
            "source scene",
        ),
        (
            lambda data: data["assets"][0].update({"source_shot_id": "scene-001-shot-002"}),
            "source shot",
        ),
        (
            lambda data: data["assets"][0].update(
                {"reference_asset_ids": [data["assets"][0]["asset_id"]]}
            ),
            "reference itself",
        ),
        (
            lambda data: data["assets"][0].update({"width": 0}),
            "greater than or equal",
        ),
        (
            lambda data: data["assets"][0].update({"width": 8193}),
            "less than or equal",
        ),
        (
            lambda data: data["assets"][0].update({"expected_duration_seconds": -1}),
            "greater than 0",
        ),
        (
            lambda data: data["assets"][0].update({"width": 1920}),
            "aspect ratio",
        ),
        (
            lambda data: data["assets"][0].update({"metadata": {"authorization_token": "unsafe"}}),
            "sensitive key",
        ),
        (
            lambda data: data["assets"][0].update({"prompt": "<script>alert(1)</script>"}),
            "unsafe executable",
        ),
        (
            lambda data: data["assets"][0].update({"prompt": "Use data:image/png;base64,AAAA"}),
            "URLs",
        ),
    ],
)
async def test_contract_rejects_invalid_identity_dimensions_and_content(
    production_scene_plan,
    mutation,
    message,
) -> None:
    _, plan = await make_plan(production_scene_plan)
    data = plan.model_dump(mode="json")
    mutation(data)
    with pytest.raises(ValidationError, match=message):
        ProductionVisualAssetPlan.model_validate(data)


@pytest.mark.asyncio
async def test_cross_validator_rejects_camera_duration_language_and_missing_primary(
    production_scene_plan,
) -> None:
    _, plan = await make_plan(production_scene_plan)
    for update, message in (
        ({"language": "en"}, "language"),
        (
            {
                "assets": (
                    plan.assets[0].model_copy(
                        update={"camera_intent": plan.assets[1].camera_intent}
                    ),
                    *plan.assets[1:],
                )
            },
            "camera",
        ),
        (
            {
                "assets": (
                    plan.assets[0].model_copy(update={"expected_duration_seconds": 6}),
                    *plan.assets[1:],
                )
            },
            "duration",
        ),
        (
            {
                "assets": tuple(
                    asset.model_copy(update={"role": "supporting"})
                    if asset.asset_id == plan.assets[0].asset_id
                    else asset
                    for asset in plan.assets
                )
            },
            "primary",
        ),
    ):
        changed = plan.model_copy(update=update)
        with pytest.raises(ValueError, match=message):
            validate_visual_asset_plan_against_scene_plan(
                changed,
                production_scene_plan,
            )


def test_public_configuration_is_strict_nested_and_historical() -> None:
    configured = VisualAssetPlanningConfiguration(
        images_per_shot=2,
        target_width=1920,
        target_height=1080,
    )
    assert configured.aspect_ratio == "16:9"
    nested = visual_asset_planning_configuration_from_snapshot(
        {"configuration": {"visual_asset_planning": configured.model_dump(mode="json")}}
    )
    assert nested == configured
    flat = visual_asset_planning_configuration_from_snapshot(
        {"configuration": {"images_per_shot": 2, "historical": True}}
    )
    assert flat.images_per_shot == 2
    with pytest.raises(ValidationError):
        VisualAssetPlanningConfiguration(provider="openrouter")
    with pytest.raises(ValidationError):
        VisualAssetPlanningConfiguration(target_width=1000, target_height=800)
    with pytest.raises(ValidationError):
        VisualAssetPlanningConfiguration(
            preferred_asset_kind="video_clip",
            allow_video_specs=False,
        )


@pytest.mark.asyncio
async def test_prompt_uses_only_scene_plan_public_config_and_strict_schema(
    production_scene_plan,
) -> None:
    request, _ = await make_plan(production_scene_plan)
    prompt = VisualAssetPlanningPromptBuilder(max_scene_plan_bytes=100_000).build(request)
    payload = json.loads(prompt.user)
    assert set(payload) == {
        "production_scene_plan",
        "visual_asset_planning_configuration",
    }
    assert "production_script" not in prompt.user
    assert "original prompt" not in prompt.user
    assert prompt.response_schema["additionalProperties"] is False
    assert "Return exclusively" in prompt.system
    with pytest.raises(ValueError, match="exceeds"):
        VisualAssetPlanningPromptBuilder(max_scene_plan_bytes=10).build(request)
