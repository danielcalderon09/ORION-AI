from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.planning.artifact_writer import InMemoryPlanningArtifactWriter
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.runtime import (
    ProductionExecutor,
    StageContext,
    StageHandlerNotFoundError,
    StageHandlerRegistrationError,
    StageHandlerRegistry,
    create_simulated_handler_registry,
)
from backend.src.production.runtime.handlers import PlanningHandler
from backend.tests.unit.production.runtime.conftest import (
    TestVideoClipBoundaryHandler,
)

NOW = datetime(2026, 7, 17, 16, 0, tzinfo=UTC)


def make_command(stage: ProductionStage) -> StageCommand:
    return StageCommand(
        command_id=UUID("20000000-0000-4000-8000-000000000301"),
        job_id=UUID("10000000-0000-4000-8000-000000000301"),
        stage=stage,
        attempt_number=1,
        idempotency_key=f"runtime:{stage.value}",
        created_at=NOW,
    )


def make_context(command: StageCommand) -> StageContext:
    return StageContext(
        job_id=command.job_id,
        command_id=command.command_id,
        stage=command.stage,
        attempt_number=command.attempt_number,
        job_prompt="Plan a short test video",
        job_configuration={},
        input_artifact_ids=command.input_artifact_ids,
        workspace_relative_path=(
            f"production/{command.job_id}/{command.stage.value}/attempt-1"
        ),
        correlation_id=command.job_id,
    )


@pytest.mark.asyncio
async def test_dispatcher_and_all_simulated_handlers_are_executable() -> None:
    ids = iter(UUID(int=value) for value in range(1, 100))
    registry = create_simulated_handler_registry(
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
        video_clip_generation_handler=TestVideoClipBoundaryHandler(
            clock=lambda: NOW,
            uuid_factory=lambda: next(ids),
        ),
    )
    executable = set(ProductionStage) - {ProductionStage.CREATED, ProductionStage.COMPLETED}
    assert registry.registered_stages == executable
    executor = ProductionExecutor(registry)
    for stage in sorted(executable, key=lambda item: item.value):
        command = make_command(stage)
        output = await executor.execute(command, make_context(command))
        assert output.result.outcome is StageOutcome.SUCCEEDED
        assert output.result.progress_percent == 100
        assert len(output.artifacts) == 1
        assert output.artifacts[0].metadata["simulated"] is True


def test_dispatcher_rejects_missing_and_duplicate_registration() -> None:
    handler = PlanningHandler(
        provider=SimulatedPlanningProvider(),
        artifact_writer=InMemoryPlanningArtifactWriter(),
        clock=lambda: NOW,
        uuid_factory=lambda: UUID(int=1),
    )
    registry = StageHandlerRegistry((handler,))
    with pytest.raises(StageHandlerNotFoundError):
        registry.resolve(ProductionStage.SCRIPTING)
    with pytest.raises(StageHandlerRegistrationError):
        StageHandlerRegistry((handler, handler))
