"""Offline contract tests for the durable video identity and prompt derivation."""

from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from backend.src.production.domain.enums import ProductionStage
from backend.src.production.image_acquisition.configuration import ImageAcquisitionConfiguration
from backend.src.production.image_acquisition.ports import ImageAcquisitionProviderRequest
from backend.src.production.image_acquisition.prompt_builder import ImageGenerationPromptBuilder
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.ports import VideoClipProviderRequest
from backend.src.production.video_clip_generation.prompt_builder import (
    VideoClipAnimationRecipeBuilder,
    VideoMotionPromptBuilder,
)
from backend.src.production.visual_asset_planning.models import (
    ContinuityEntityKind,
    ProductionVisualAssetPlan,
    RecurringCharacter,
    VideoIdentity,
    VisualConsistencyProfile,
    VisualContinuityEntity,
    derive_video_identity,
)
from backend.src.production.visual_asset_planning.prompt_derivation import (
    build_scene_visual_prompt,
)
from backend.tests.unit.production.image_acquisition.conftest import make_visual_asset


@pytest.fixture
def visual_asset_plan() -> ProductionVisualAssetPlan:
    assets = (make_visual_asset(), make_visual_asset("asset-s001-q002-v001", shot_number=2))
    return ProductionVisualAssetPlan(
        source_scene_plan_schema_version="1.0.0",
        source_scene_plan_artifact_id=UUID("40000000-0000-4000-8000-000000000702"),
        source_scene_plan_sha256="a" * 64,
        title="Ocean mysteries",
        language="es",
        aspect_ratio="1:1",
        global_visual_direction="Cinematic documentary continuity",
        global_negative_prompt="No logos and no embedded text",
        consistency_profile=VisualConsistencyProfile(
            entities=(
                VisualContinuityEntity(
                    entity_id="location_01",
                    kind=ContinuityEntityKind.LOCATION,
                    description="The same deep ocean expedition setting",
                ),
            ),
            palette=("deep blue", "muted amber"),
            lighting_direction="Consistent volumetric underwater light",
            style_direction="Naturalistic documentary camera language",
            period="Contemporary",
            visual_identity="Cinematic deep-ocean documentary",
            continuity_rules=("Preserve the expedition visual language",),
        ),
        assets=assets,
    )


def _video_request(identity: VideoIdentity, metadata: dict[str, object] | None = None):
    content = b"safe-source-image"
    configuration = VideoClipGenerationConfiguration(
        provider="openrouter",
        model="test/video-model",
        duration_seconds=4,
        resolution="720p",
    )
    return VideoClipProviderRequest(
        job_id=UUID("10000000-0000-4000-8000-000000000701"),
        command_id=UUID("20000000-0000-4000-8000-000000000701"),
        correlation_id=UUID("30000000-0000-4000-8000-000000000701"),
        attempt_number=1,
        visual_asset_id="asset-s001-q001-v001",
        source_image_artifact_id=UUID("40000000-0000-4000-8000-000000000701"),
        source_image_sha256=hashlib.sha256(content).hexdigest(),
        source_image_mime_type="image/png",
        source_image_size_bytes=len(content),
        source_image_width=64,
        source_image_height=64,
        source_role="primary",
        source_metadata=metadata or {},
        video_identity=identity,
        source_image_content=content,
        duration_seconds=4,
        frame_rate=24,
        width=720,
        height=1280,
        configuration=configuration,
        fingerprint=configuration.fingerprint(),
    )


def test_identity_is_derived_once_and_shared_by_two_scene_intents(visual_asset_plan):
    identity = derive_video_identity(visual_asset_plan)
    first, second = visual_asset_plan.assets
    second = second.model_copy(update={"visual_subject": "A distant moon"})

    first_prompt = build_scene_visual_prompt(identity, first)
    second_prompt = build_scene_visual_prompt(identity, second)

    assert first_prompt.split("SCENE-SPECIFIC CONTENT:", 1)[0] == second_prompt.split(
        "SCENE-SPECIFIC CONTENT:", 1
    )[0]
    assert "Geometric skyline" in first_prompt
    assert "A distant moon" in second_prompt


def test_recurring_character_contract_is_stable_across_prompts(visual_asset_plan):
    identity = derive_video_identity(visual_asset_plan).model_copy(
        update={
            "recurring_characters": (
                RecurringCharacter(
                    character_id="character_01",
                    role="expedition lead",
                    appearance="short dark hair and a red scarf",
                    wardrobe="navy exploration suit",
                    continuity_notes=("same face and wardrobe in every scene",),
                ),
            )
        }
    )
    rendered = build_scene_visual_prompt(identity, visual_asset_plan.assets[0])
    assert "character_01" in rendered
    assert "same face and wardrobe" in rendered


def test_negative_constraints_are_shared_and_scene_specific(visual_asset_plan):
    identity = derive_video_identity(visual_asset_plan)
    rendered = build_scene_visual_prompt(identity, visual_asset_plan.assets[0])
    assert "No logos and no embedded text" in rendered
    assert "No text artifacts" in rendered
    assert "CONTINUITY CONSTRAINTS:" in rendered
    assert "SCENE-SPECIFIC CONTENT:" in rendered


def test_same_input_has_exactly_deterministic_image_and_video_prompts(visual_asset_plan):
    identity = derive_video_identity(visual_asset_plan)
    image_a = build_scene_visual_prompt(identity, visual_asset_plan.assets[0])
    image_b = build_scene_visual_prompt(identity, visual_asset_plan.assets[0])
    request = _video_request(identity)
    video_builder = VideoMotionPromptBuilder()

    assert image_a == image_b
    assert video_builder.build(request) == video_builder.build(request)


def test_historical_plan_without_identity_remains_readable(visual_asset_plan):
    historical = visual_asset_plan.model_dump(mode="json", exclude={"video_identity"})
    loaded = type(visual_asset_plan).model_validate(historical)
    assert loaded.video_identity is None
    assert loaded.assets == visual_asset_plan.assets


def test_video_prompt_includes_identity_and_preserves_motion(visual_asset_plan):
    identity = derive_video_identity(visual_asset_plan)
    built = VideoMotionPromptBuilder().build(_video_request(identity))
    assert "CONTINUITY CONSTRAINTS:" in built.text
    assert "Generate no audio" in built.text


def test_image_prompt_uses_the_same_identity_contract(visual_asset_plan):
    identity = derive_video_identity(visual_asset_plan)
    request = ImageAcquisitionProviderRequest(
        job_id=UUID("10000000-0000-4000-8000-000000000701"),
        command_id=UUID("20000000-0000-4000-8000-000000000701"),
        correlation_id=UUID("30000000-0000-4000-8000-000000000701"),
        attempt_number=1,
        visual_asset=visual_asset_plan.assets[0],
        video_identity=identity,
        configuration=ImageAcquisitionConfiguration(output_format="png"),
    )
    built = ImageGenerationPromptBuilder().build(request)
    assert "CONTINUITY CONSTRAINTS:" in built.text
    assert "SCENE-SPECIFIC CONTENT:" in built.text
    assert "Cinematic deep-ocean documentary" in built.text


def test_video_playback_recipe_remains_single_pass():
    recipe = VideoClipAnimationRecipeBuilder().build("asset-s001-q001-v001")
    assert recipe["deterministic"] is True
    assert recipe["version"] == "simulated-motion-v1"


def test_audio_first_stage_order_is_preserved():
    from backend.src.production.application.orchestration.stage_registry import (
        StageRegistry,
    )

    stages = StageRegistry.active_stages(generate_clips_after_render=False)
    assert stages.index(ProductionStage.GENERATING_NARRATION) < stages.index(
        ProductionStage.GENERATING_VIDEO_CLIPS
    )
