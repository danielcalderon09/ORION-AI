from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType, ProductionStage
from backend.src.production.runtime import (
    ProductionExecutor,
    StageContext,
    StageContextMismatchError,
    StageExecutionContractError,
    StageExecutionOutput,
    StageHandlerRegistry,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000381")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000381")
ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000381")


def make_command() -> StageCommand:
    return StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.PLANNING,
        attempt_number=1,
        idempotency_key="executor:test",
        created_at=NOW,
    )


def make_context(**updates) -> StageContext:
    values = {
        "job_id": JOB_ID,
        "command_id": COMMAND_ID,
        "stage": ProductionStage.PLANNING,
        "attempt_number": 1,
        "workspace_relative_path": f"production/{JOB_ID}/planning/attempt-1",
        "correlation_id": JOB_ID,
    }
    values.update(updates)
    return StageContext(**values)


def make_artifact(artifact_id: UUID = ARTIFACT_ID) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        job_id=JOB_ID,
        artifact_type=ArtifactType.MANIFEST,
        relative_path="simulated/planning/output.json",
        mime_type="application/json",
        status=ArtifactStatus.READY,
    )


def make_result(output_ids: tuple[UUID, ...]) -> StageResult:
    return StageResult(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.PLANNING,
        outcome=StageOutcome.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
        progress_percent=100,
        output_artifact_ids=output_ids,
    )


class OutputHandler:
    supported_stages = frozenset({ProductionStage.PLANNING})

    def __init__(self, output: StageExecutionOutput) -> None:
        self.output = output
        self.calls = 0
        self.context: StageContext | None = None

    async def execute(self, command, context) -> StageExecutionOutput:
        self.calls += 1
        self.context = context
        return self.output


class MutatingHandler(OutputHandler):
    async def execute(self, command, context) -> StageExecutionOutput:
        context.job_configuration["mutated"] = True
        return await super().execute(command, context)


@pytest.mark.asyncio
async def test_executor_rejects_context_mismatch_before_handler() -> None:
    handler = OutputHandler(StageExecutionOutput(result=make_result(())))
    executor = ProductionExecutor(StageHandlerRegistry((handler,)))
    with pytest.raises(StageContextMismatchError):
        await executor.execute(
            make_command(),
            make_context(command_id=UUID("20000000-0000-4000-8000-000000000399")),
        )
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_executor_runs_one_handler_and_passes_context() -> None:
    output = StageExecutionOutput(
        result=make_result((ARTIFACT_ID,)),
        artifacts=(make_artifact(),),
    )
    handler = OutputHandler(output)
    executor = ProductionExecutor(StageHandlerRegistry((handler,)))
    context = make_context()
    assert await executor.execute(make_command(), context) == output
    assert handler.calls == 1
    assert handler.context == context


@pytest.mark.asyncio
async def test_executor_rejects_duplicate_and_inconsistent_artifacts() -> None:
    duplicate = OutputHandler(
        StageExecutionOutput(
            result=make_result((ARTIFACT_ID,)),
            artifacts=(make_artifact(), make_artifact()),
        )
    )
    with pytest.raises(StageExecutionContractError, match="duplicate"):
        await ProductionExecutor(StageHandlerRegistry((duplicate,))).execute(
            make_command(), make_context()
        )

    other_id = UUID("30000000-0000-4000-8000-000000000399")
    inconsistent = OutputHandler(
        StageExecutionOutput(
            result=make_result((other_id,)),
            artifacts=(make_artifact(),),
        )
    )
    with pytest.raises(StageExecutionContractError, match="exactly match"):
        await ProductionExecutor(StageHandlerRegistry((inconsistent,))).execute(
            make_command(), make_context()
        )


@pytest.mark.asyncio
async def test_executor_rejects_handler_context_mutation() -> None:
    handler = MutatingHandler(StageExecutionOutput(result=make_result(())))
    with pytest.raises(StageExecutionContractError, match="modified StageContext"):
        await ProductionExecutor(StageHandlerRegistry((handler,))).execute(
            make_command(), make_context()
        )
