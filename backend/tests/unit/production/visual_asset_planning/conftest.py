"""Deterministic visual asset planning fixtures."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScene,
    ProductionScenePlan,
    ProductionShot,
    ProductionTiming,
    ProductionTransition,
)

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000801")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000801")
SCENE_PLAN_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000801")
OUTPUT_ARTIFACT_ID = UUID("40000000-0000-4000-8000-000000000801")


@pytest.fixture
def production_scene_plan() -> ProductionScenePlan:
    scenes = []
    for scene_number in (1, 2):
        shots = []
        for shot_number in (1, 2):
            start = float((shot_number - 1) * 5)
            shots.append(
                ProductionShot(
                    shot_id=(f"scene-{scene_number:03d}-shot-{shot_number:03d}"),
                    shot_number=shot_number,
                    scene_number=scene_number,
                    objective=f"Objetivo visual {scene_number}.{shot_number}",
                    description=f"Plano urbano seguro {scene_number}.{shot_number}",
                    camera=ProductionCamera(
                        framing="wide" if shot_number == 1 else "medium",
                        angle="eye_level",
                        movement="static",
                        lens_millimeters=35 if shot_number == 1 else 50,
                        subject=f"Sujeto aprobado {scene_number}.{shot_number}",
                    ),
                    timing=ProductionTiming(
                        start_seconds=start,
                        duration_seconds=5,
                        end_seconds=start + 5,
                    ),
                    transition=ProductionTransition(
                        kind=("none" if scene_number == 2 and shot_number == 2 else "cut"),
                        duration_seconds=0,
                    ),
                )
            )
        scenes.append(
            ProductionScene(
                scene_id=f"scene-{scene_number:03d}",
                scene_number=scene_number,
                source_scene_number=scene_number,
                title=f"Escena {scene_number}",
                narration=f"Narración aprobada {scene_number}.",
                objective=f"Ambiente aprobado {scene_number}",
                estimated_duration_seconds=10,
                shots=tuple(shots),
            )
        )
    return ProductionScenePlan(
        source_script_schema_version="1.0.0",
        source_script_sha256="b" * 64,
        title="Bogotá visual",
        language="es",
        target_duration_seconds=20,
        scenes=tuple(scenes),
    )


@pytest.fixture
def visual_asset_command_context():
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.VISUAL_ASSET_PLANNING,
        attempt_number=1,
        idempotency_key="visual-asset-planning:test",
        input_artifact_ids=(SCENE_PLAN_ARTIFACT_ID,),
        configuration_snapshot={
            "configuration": {
                "visual_asset_planning": {
                    "images_per_shot": 1,
                    "target_width": 1080,
                    "target_height": 1920,
                }
            }
        },
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.VISUAL_ASSET_PLANNING,
        attempt_number=1,
        input_artifact_ids=command.input_artifact_ids,
        workspace_relative_path=(f"production/{JOB_ID}/visual_asset_planning/attempt-1"),
        correlation_id=JOB_ID,
    )
    return command, context
