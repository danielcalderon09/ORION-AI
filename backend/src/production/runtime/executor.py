"""One-command production executor with no persistence responsibility."""

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.orchestration import validate_stage_result
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext, StageContextMismatchError
from backend.src.production.runtime.job_dispatcher import StageHandlerRegistry
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class StageExecutionContractError(ValueError):
    """Raised when a handler returns output inconsistent with its command."""


class ProductionExecutor:
    """Resolve and execute exactly one handler for exactly one command."""

    def __init__(self, registry: StageHandlerRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        self._validate_context(command, context)
        command_snapshot = command.model_dump_json()
        context_snapshot = context.model_dump_json()
        output = await self._registry.resolve(command.stage).execute(command, context)
        if command.model_dump_json() != command_snapshot:
            raise StageExecutionContractError("handler modified StageCommand")
        if context.model_dump_json() != context_snapshot:
            raise StageExecutionContractError("handler modified StageContext")
        validate_stage_result(command, output.result)
        artifact_ids = tuple(artifact.artifact_id for artifact in output.artifacts)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise StageExecutionContractError("handler returned duplicate artifact IDs")
        if artifact_ids != output.result.output_artifact_ids:
            raise StageExecutionContractError(
                "handler artifacts must exactly match StageResult output_artifact_ids"
            )
        if any(artifact.job_id != command.job_id for artifact in output.artifacts):
            raise StageExecutionContractError("handler artifact belongs to another job")
        for artifact in output.artifacts:
            validate_relative_path(artifact.relative_path)
        return output

    @staticmethod
    def _validate_context(command: StageCommand, context: StageContext) -> None:
        pairs = (
            (context.job_id, command.job_id, "job_id"),
            (context.command_id, command.command_id, "command_id"),
            (context.stage, command.stage, "stage"),
            (context.attempt_number, command.attempt_number, "attempt_number"),
        )
        for actual, expected, field_name in pairs:
            if actual != expected:
                raise StageContextMismatchError(
                    f"StageContext {field_name} does not match StageCommand"
                )
