"""Shared deterministic scripting fixtures."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.planning.models import ProductionPlan, ProductionScenePlan
from backend.src.production.runtime.context import StageContext
from backend.src.production.scripting.configuration import ScriptingConfiguration
from backend.src.production.scripting.ports import ScriptingProviderRequest

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000601")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000601")
PLAN_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000601")


@pytest.fixture
def production_plan() -> ProductionPlan:
    return ProductionPlan(
        title="Solar eclipse",
        summary="A clear explanation of a solar eclipse.",
        language="en",
        target_duration_seconds=20,
        aspect_ratio="9:16",
        visual_style="cinematic",
        narrative_style="educational",
        scenes=tuple(
            ProductionScenePlan(
                scene_number=index,
                title=f"Scene {index}",
                narration=f"Useful narration for scene {index}.",
                visual_description=f"Visual intent {index}",
                image_prompt=f"Image prompt {index}",
                motion_instruction="Slow zoom",
                estimated_duration_seconds=10,
                transition="cut",
                on_screen_text=f"Text {index}",
                metadata={},
            )
            for index in (1, 2)
        ),
        metadata={},
    )


@pytest.fixture
def scripting_command_context():
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.SCRIPTING,
        attempt_number=1,
        idempotency_key="scripting:test",
        input_artifact_ids=(PLAN_ARTIFACT_ID,),
        configuration_snapshot={"configuration": {"scripting": {"tone": "calm"}}},
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.SCRIPTING,
        attempt_number=1,
        job_configuration=command.configuration_snapshot,
        input_artifact_ids=command.input_artifact_ids,
        workspace_relative_path=f"production/{JOB_ID}/scripting/attempt-1",
        correlation_id=JOB_ID,
    )
    return command, context


@pytest.fixture
def scripting_request(production_plan) -> ScriptingProviderRequest:
    return ScriptingProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        plan=production_plan,
        configuration=ScriptingConfiguration(tone="calm"),
        language="en",
        target_duration_seconds=20,
    )
