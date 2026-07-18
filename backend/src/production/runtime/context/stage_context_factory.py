"""Pure construction of stable stage execution contexts."""

from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.runtime.context.stage_context import StageContext


class StageContextMismatchError(ValueError):
    """Raised when durable job and command identities do not agree."""


class StageContextFactory:
    def create(
        self,
        *,
        job: ProductionJob,
        command: StageCommand,
        correlation_id: UUID | None = None,
    ) -> StageContext:
        if command.job_id != job.job_id:
            raise StageContextMismatchError("StageCommand belongs to another ProductionJob")
        if command.stage != job.current_stage:
            raise StageContextMismatchError("StageCommand stage does not match ProductionJob")
        return StageContext(
            job_id=job.job_id,
            command_id=command.command_id,
            stage=command.stage,
            attempt_number=command.attempt_number,
            job_configuration=dict(command.configuration_snapshot),
            input_artifact_ids=command.input_artifact_ids,
            workspace_relative_path=(
                f"production/{job.job_id}/{command.stage.value}/"
                f"attempt-{command.attempt_number}"
            ),
            correlation_id=correlation_id or job.job_id,
            metadata={"idempotency_key": command.idempotency_key},
        )
