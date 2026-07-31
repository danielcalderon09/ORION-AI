import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers import DurableSubtitleHandler
from backend.src.production.scene_planning.ports import ReadProductionScript
from backend.src.production.scripting.models import ProductionScript, ProductionScriptScene

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
JOB_ID = UUID("90000000-0000-4000-8000-000000000011")
COMMAND_ID = UUID("90000000-0000-4000-8000-000000000012")
SCRIPT_ID = UUID("90000000-0000-4000-8000-000000000013")


class ScriptReader:
    async def read_for_scene_planning(self, *, context: StageContext) -> ReadProductionScript:
        assert context.job_id == JOB_ID
        script = ProductionScript(
            source_plan_schema_version="1.0.0",
            title="Marte",
            language="es",
            target_duration_seconds=8,
            tone="informativo",
            opening_hook="Marte sorprende.",
            scenes=(
                ProductionScriptScene(
                    scene_number=1,
                    source_scene_number=1,
                    heading="Uno",
                    narration="Primera curiosidad.",
                    estimated_duration_seconds=4,
                    delivery_style="claro",
                    visual_intent="Marte",
                ),
                ProductionScriptScene(
                    scene_number=2,
                    source_scene_number=2,
                    heading="Dos",
                    narration="Segunda curiosidad.",
                    estimated_duration_seconds=4,
                    delivery_style="claro",
                    visual_intent="Superficie",
                ),
            ),
        )
        return ReadProductionScript(
            script=script,
            artifact_id=SCRIPT_ID,
            relative_path=f"production/{JOB_ID}/scripting/attempt-1/production-script.json",
            sha256="a" * 64,
            size_bytes=100,
            schema_version="1.0.0",
        )


def _command_and_context() -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.GENERATING_SUBTITLES,
        attempt_number=1,
        idempotency_key="b" * 64,
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.GENERATING_SUBTITLES,
        attempt_number=1,
        workspace_relative_path=(f"production/{JOB_ID}/generating_subtitles/attempt-1"),
        correlation_id=JOB_ID,
    )
    return command, context


@pytest.mark.asyncio
async def test_durable_subtitle_is_valid_nonempty_and_idempotent(tmp_path) -> None:
    handler = DurableSubtitleHandler(
        script_reader=ScriptReader(),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        clock=lambda: NOW,
    )
    command, context = _command_and_context()
    first = await handler.execute(command, context)
    second = await handler.execute(command, context)
    assert first == second
    assert first.result.outcome is StageOutcome.SUCCEEDED
    assert len(first.artifacts) == 1
    artifact = first.artifacts[0]
    assert artifact.artifact_type is ArtifactType.SUBTITLES
    assert artifact.size_bytes is not None and artifact.size_bytes > 0
    path = tmp_path / artifact.relative_path
    content = path.read_bytes()
    assert content.decode("utf-8") == (
        "1\n00:00:00,000 --> 00:00:04,000\nPrimera curiosidad.\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\nSegunda curiosidad.\n"
    )
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
