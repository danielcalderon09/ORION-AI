"""Deterministic scene-planning fixtures."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.scripting.models import ProductionScript, ProductionScriptScene

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000701")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000701")
SCRIPT_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000701")


@pytest.fixture
def production_script() -> ProductionScript:
    return ProductionScript(
        source_plan_schema_version="1.0.0",
        title="Bogotá nocturna",
        language="es",
        target_duration_seconds=20,
        tone="cinematic",
        opening_hook="La ciudad despierta bajo las luces.",
        scenes=tuple(
            ProductionScriptScene(
                scene_number=index,
                source_scene_number=index,
                heading=f"Escena {index}",
                narration=f"Narración aprobada para la escena {index}.",
                estimated_duration_seconds=10,
                delivery_style="calm",
                pronunciation_notes=(),
                on_screen_text=f"Texto {index}",
                visual_intent=f"Vista urbana segura {index}",
                transition_note="cut",
                metadata={"simulated": True},
            )
            for index in (1, 2)
        ),
        metadata={"simulated": True},
    )


@pytest.fixture
def scene_planning_command_context():
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.SCENE_PLANNING,
        attempt_number=1,
        idempotency_key="scene-planning:test",
        input_artifact_ids=(SCRIPT_ARTIFACT_ID,),
        configuration_snapshot={},
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.SCENE_PLANNING,
        attempt_number=1,
        input_artifact_ids=command.input_artifact_ids,
        workspace_relative_path=f"production/{JOB_ID}/scene_planning/attempt-1",
        correlation_id=JOB_ID,
    )
    return command, context
