"""Contracts and reusable behavior for simulated production stage handlers."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class StageHandler(Protocol):
    """Execute one command without owning persistence or orchestration."""

    supported_stages: frozenset[ProductionStage]

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput: ...


class SimulatedStageHandler:
    """Deterministic, non-blocking handler used until real adapters exist."""

    supported_stages: frozenset[ProductionStage]
    artifact_type: ArtifactType
    mime_type: str
    extension: str

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        outcomes: tuple[StageOutcome, ...] = (StageOutcome.SUCCEEDED,),
    ) -> None:
        if not outcomes:
            raise ValueError("outcomes must not be empty")
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._outcomes = outcomes
        self._execution_count = 0

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        if command.stage not in self.supported_stages:
            raise ValueError(f"handler does not support stage {command.stage.value}")
        if context.command_id != command.command_id:
            raise ValueError("StageContext does not belong to StageCommand")
        started_at = self._aware_now()
        await asyncio.sleep(0)
        outcome = self._outcomes[min(self._execution_count, len(self._outcomes) - 1)]
        self._execution_count += 1
        artifacts: tuple[Artifact, ...] = ()
        error_code: str | None = None
        error_message: str | None = None
        retry_after_seconds: float | None = None
        progress = 100.0
        if outcome is StageOutcome.SUCCEEDED:
            artifact = Artifact(
                artifact_id=self._uuid_factory(),
                job_id=command.job_id,
                artifact_type=self.artifact_type,
                relative_path=(
                    f"simulated/{command.stage.value}/"
                    f"{command.command_id}.{self.extension}"
                ),
                mime_type=self.mime_type,
                status=ArtifactStatus.READY,
                size_bytes=0,
                provider="orion-simulated-runtime",
                model_version="phase-3",
                metadata={"command_id": str(command.command_id), "simulated": True},
            )
            artifacts = (artifact,)
        elif outcome is StageOutcome.FAILED_TRANSIENT:
            progress = 25.0
            error_code = "simulated_transient_failure"
            error_message = "Simulated transient stage failure"
            retry_after_seconds = 1.0
        elif outcome is StageOutcome.FAILED_PERMANENT:
            progress = 25.0
            error_code = "simulated_permanent_failure"
            error_message = "Simulated permanent stage failure"
        elif outcome is StageOutcome.NEEDS_USER_ACTION:
            progress = 25.0
            error_code = "simulated_user_action"
            error_message = "Simulated user action required"
        else:
            progress = 25.0

        result = StageResult(
            command_id=command.command_id,
            job_id=command.job_id,
            stage=command.stage,
            outcome=outcome,
            started_at=started_at,
            finished_at=self._aware_now(),
            progress_percent=progress,
            output_artifact_ids=tuple(item.artifact_id for item in artifacts),
            error_code=error_code,
            error_message=error_message,
            retry_after_seconds=retry_after_seconds,
            metadata={
                "handler": type(self).__name__,
                "simulated": True,
                "workspace": context.workspace_relative_path,
            },
        )
        return StageExecutionOutput(result=result, artifacts=artifacts)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("handler clock must return a timezone-aware datetime")
        return value
