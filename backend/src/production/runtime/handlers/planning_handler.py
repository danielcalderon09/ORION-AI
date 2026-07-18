"""Provider-driven PLANNING stage handler."""

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.planning.artifact_writer import PlanningArtifactWriter
from backend.src.production.planning.exceptions import (
    PlanningProviderAuthenticationError,
    PlanningProviderConfigurationError,
    PlanningProviderContractError,
    PlanningProviderError,
    PlanningProviderRateLimitError,
    PlanningProviderResponseError,
    PlanningProviderTimeoutError,
    PlanningProviderUnavailableError,
)
from backend.src.production.planning.models import PlanningJobConfiguration
from backend.src.production.planning.ports import PlanningProvider, PlanningProviderRequest
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput

logger = logging.getLogger(__name__)


class PlanningHandler:
    supported_stages = frozenset({ProductionStage.PLANNING})

    def __init__(
        self,
        *,
        provider: PlanningProvider,
        artifact_writer: PlanningArtifactWriter,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        prompt_version: str = PlanningPromptBuilder.planning_prompt_version,
    ) -> None:
        self._provider = provider
        self._writer = artifact_writer
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._prompt_version = prompt_version

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        if command.stage is not ProductionStage.PLANNING:
            raise ValueError("PlanningHandler only supports PLANNING")
        started_at = self._aware_now()
        try:
            request = self._request(command, context)
            response = await self._provider.generate_plan(request)
            written = await self._writer.write_plan(context=context, plan=response.plan)
        except (
            PlanningProviderTimeoutError,
            PlanningProviderRateLimitError,
            PlanningProviderUnavailableError,
            OSError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                error_code=self._error_code(exc),
                retry_after_seconds=1.0,
            )
        except (
            PlanningProviderConfigurationError,
            PlanningProviderAuthenticationError,
            ValidationError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                error_code=self._error_code(exc),
            )
        except (
            PlanningProviderContractError,
            PlanningProviderResponseError,
            PlanningProviderError,
            ValueError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                error_code=self._error_code(exc),
            )

        public_metadata = {
            **response.metadata,
            "schema_version": response.plan.schema_version,
            "prompt_version": self._prompt_version,
            "scene_count": len(response.plan.scenes),
            "latency_ms": response.latency_ms,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.total_tokens,
            "request_id": response.request_id,
        }
        public_metadata = {
            key: value for key, value in public_metadata.items() if value is not None
        }
        artifact = Artifact(
            artifact_id=self._uuid_factory(),
            job_id=command.job_id,
            artifact_type=ArtifactType.PRODUCTION_PLAN,
            relative_path=written.relative_path,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=written.size_bytes,
            sha256=written.sha256,
            provider=response.provider,
            model_version=response.model,
            metadata=public_metadata,
        )
        logger.info(
            "production planning completed",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "stage": command.stage.value,
                "attempt_number": command.attempt_number,
                "provider": response.provider,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "scene_count": len(response.plan.scenes),
                "artifact_size": written.size_bytes,
                "prompt_hash": hashlib.sha256(context.job_prompt.encode()).hexdigest()[:12],
                "prompt_length": len(context.job_prompt),
            },
        )
        result = StageResult(
            command_id=command.command_id,
            job_id=command.job_id,
            stage=command.stage,
            outcome=StageOutcome.SUCCEEDED,
            started_at=started_at,
            finished_at=self._aware_now(),
            progress_percent=100,
            output_artifact_ids=(artifact.artifact_id,),
            metadata={
                "handler": type(self).__name__,
                "provider": response.provider,
                "model": response.model,
                "scene_count": len(response.plan.scenes),
            },
        )
        return StageExecutionOutput(result=result, artifacts=(artifact,))

    def _request(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> PlanningProviderRequest:
        if not context.job_prompt.strip():
            raise PlanningProviderConfigurationError("production prompt is missing")
        raw_configuration = command.configuration_snapshot.get("configuration", {})
        configuration = PlanningJobConfiguration.model_validate(raw_configuration)
        return PlanningProviderRequest(
            job_id=command.job_id,
            prompt=context.job_prompt,
            configuration=configuration,
            target_duration_seconds=configuration.target_duration_seconds,
            language=configuration.language,
            aspect_ratio=configuration.aspect_ratio,
            correlation_id=context.correlation_id,
            attempt_number=command.attempt_number,
        )

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        *,
        error_code: str,
        retry_after_seconds: float | None = None,
    ) -> StageExecutionOutput:
        result = StageResult(
            command_id=command.command_id,
            job_id=command.job_id,
            stage=command.stage,
            outcome=outcome,
            started_at=started_at,
            finished_at=self._aware_now(),
            progress_percent=0,
            error_code=error_code,
            error_message="Planning stage could not complete",
            retry_after_seconds=retry_after_seconds,
            metadata={"handler": type(self).__name__, "error_category": error_code},
        )
        return StageExecutionOutput(result=result)

    @staticmethod
    def _error_code(error: Exception) -> str:
        mapping: tuple[tuple[type[Exception], str], ...] = (
            (PlanningProviderTimeoutError, "planning_provider_timeout"),
            (PlanningProviderRateLimitError, "planning_provider_rate_limit"),
            (PlanningProviderUnavailableError, "planning_provider_unavailable"),
            (PlanningProviderAuthenticationError, "planning_provider_authentication"),
            (PlanningProviderConfigurationError, "planning_provider_configuration"),
            (PlanningProviderContractError, "planning_provider_contract"),
            (PlanningProviderResponseError, "planning_provider_response"),
            (OSError, "planning_artifact_write"),
            (ValidationError, "planning_configuration_invalid"),
        )
        return next(
            (code for error_type, code in mapping if isinstance(error, error_type)),
            "planning_stage_error",
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("handler clock must return a timezone-aware datetime")
        return value
