"""Deterministic Phase 5E.2 fixtures."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.image_acquisition.ports import ReadProductionVisualAssetPlan
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.models import ProductionCamera
from backend.src.production.visual_asset_planning.models import (
    AssetKind,
    ContinuityEntityKind,
    GenerationMode,
    ProductionVisualAssetPlan,
    ProductionVisualAssetSpec,
    SeedPolicy,
    VisualAssetRole,
    VisualComposition,
    VisualConsistencyProfile,
    VisualContinuityEntity,
)

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000901")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000901")
VISUAL_PLAN_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000901")


def make_visual_asset(
    asset_id: str = "asset-s001-q001-v001",
    *,
    shot_number: int = 1,
) -> ProductionVisualAssetSpec:
    return ProductionVisualAssetSpec(
        asset_id=asset_id,
        scene_number=1,
        source_scene_id="scene-001",
        shot_number=shot_number,
        source_shot_id=f"scene-001-shot-{shot_number:03d}",
        role=VisualAssetRole.PRIMARY,
        asset_kind=AssetKind.STILL_IMAGE,
        generation_mode=GenerationMode.TEXT_TO_IMAGE,
        prompt=f"Approved safe skyline shot {shot_number}",
        negative_prompt="No text artifacts",
        visual_subject="Geometric skyline",
        environment="Safe simulated city",
        composition=VisualComposition(
            layout="Centered composition",
            focal_point="Central tower",
            depth="Foreground and background layers",
            action="Static establishing view",
        ),
        camera_intent=ProductionCamera(
            framing="wide",
            angle="eye_level",
            movement="static",
            lens_millimeters=35,
            subject="Geometric skyline",
        ),
        lighting="Soft daylight",
        color_direction="Blue and amber",
        style_direction="Simple cinematic geometry",
        continuity_group="location_01",
        width=64,
        height=64,
        aspect_ratio="1:1",
        expected_duration_seconds=4,
        seed_policy=SeedPolicy.DETERMINISTIC,
        safety_notes=("Safe content only",),
    )


@pytest.fixture
def visual_asset_plan() -> ProductionVisualAssetPlan:
    return ProductionVisualAssetPlan(
        source_scene_plan_schema_version="1.0.0",
        source_scene_plan_artifact_id=UUID(
            "40000000-0000-4000-8000-000000000901"
        ),
        source_scene_plan_sha256="a" * 64,
        title="Simulated image acquisition",
        language="en",
        aspect_ratio="1:1",
        global_visual_direction="Deterministic visual direction",
        global_negative_prompt="No unsafe content",
        consistency_profile=VisualConsistencyProfile(
            entities=(
                VisualContinuityEntity(
                    entity_id="location_01",
                    kind=ContinuityEntityKind.LOCATION,
                    description="Stable simulated location",
                ),
            ),
            palette=("blue", "amber"),
            lighting_direction="Stable daylight",
            style_direction="Simple geometry",
            period="Contemporary",
            visual_identity="ORION test identity",
            continuity_rules=("Keep the location stable",),
        ),
        assets=(
            make_visual_asset(),
            make_visual_asset(
                "asset-s001-q002-v001",
                shot_number=2,
            ),
        ),
        metadata={"simulated": True},
    )


@pytest.fixture
def source_visual_plan(
    visual_asset_plan: ProductionVisualAssetPlan,
) -> ReadProductionVisualAssetPlan:
    return ReadProductionVisualAssetPlan(
        visual_asset_plan=visual_asset_plan,
        job_id=JOB_ID,
        artifact_id=VISUAL_PLAN_ARTIFACT_ID,
        relative_path=(
            f"production/{JOB_ID}/visual_asset_planning/attempt-1/"
            "visual-asset-plan.json"
        ),
        sha256="b" * 64,
        size_bytes=123,
        schema_version="1.0.0",
        provider="orion-simulated",
        model_version="visual-asset-planning-simulator-v1",
        created_at=NOW,
    )


@pytest.fixture
def image_command_context():
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.ACQUIRING_ASSETS,
        attempt_number=1,
        idempotency_key="image-acquisition:test",
        input_artifact_ids=(VISUAL_PLAN_ARTIFACT_ID,),
        configuration_snapshot={},
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.ACQUIRING_ASSETS,
        attempt_number=1,
        input_artifact_ids=command.input_artifact_ids,
        workspace_relative_path=(
            f"production/{JOB_ID}/acquiring_assets/attempt-1"
        ),
        correlation_id=JOB_ID,
    )
    return command, context
