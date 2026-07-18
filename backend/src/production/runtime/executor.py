"""One-command production executor with no persistence responsibility."""

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.orchestration import validate_stage_result
from backend.src.production.runtime.job_dispatcher import StageHandlerRegistry
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class StageExecutionContractError(ValueError):
    """Raised when a handler returns output inconsistent with its command."""


class ProductionExecutor:
    """Resolve and execute exactly one handler for exactly one command."""

    def __init__(self, registry: StageHandlerRegistry) -> None:
        self._registry = registry

    async def execute(self, command: StageCommand) -> StageExecutionOutput:
        output = await self._registry.resolve(command.stage).execute(command)
        validate_stage_result(command, output.result)
        artifact_ids = tuple(artifact.artifact_id for artifact in output.artifacts)
        if artifact_ids != output.result.output_artifact_ids:
            raise StageExecutionContractError(
                "handler artifacts must exactly match StageResult output_artifact_ids"
            )
        if any(artifact.job_id != command.job_id for artifact in output.artifacts):
            raise StageExecutionContractError("handler artifact belongs to another job")
        return output
