from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.runtime import (
    ProductionExecutor,
    StageHandlerNotFoundError,
    StageHandlerRegistrationError,
    StageHandlerRegistry,
    create_simulated_handler_registry,
)
from backend.src.production.runtime.handlers import PlanningHandler

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


@pytest.mark.asyncio
async def test_dispatcher_and_all_simulated_handlers_are_executable() -> None:
    ids = iter(UUID(int=value) for value in range(1, 100))
    registry = create_simulated_handler_registry(
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
    )
    executable = set(ProductionStage) - {ProductionStage.CREATED, ProductionStage.COMPLETED}
    assert registry.registered_stages == executable
    executor = ProductionExecutor(registry)
    for stage in sorted(executable, key=lambda item: item.value):
        output = await executor.execute(make_command(stage))
        assert output.result.outcome is StageOutcome.SUCCEEDED
        assert output.result.progress_percent == 100
        assert len(output.artifacts) == 1
        assert output.artifacts[0].metadata["simulated"] is True


def test_dispatcher_rejects_missing_and_duplicate_registration() -> None:
    handler = PlanningHandler(clock=lambda: NOW, uuid_factory=lambda: UUID(int=1))
    registry = StageHandlerRegistry((handler,))
    with pytest.raises(StageHandlerNotFoundError):
        registry.resolve(ProductionStage.SCRIPTING)
    with pytest.raises(StageHandlerRegistrationError):
        StageHandlerRegistry((handler, handler))
