"""Durable, provider-driven SCRIPTING stage handler."""

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
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers.base import SimulatedStageHandler
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.scripting.artifact_writer import ScriptingArtifactWriter
from backend.src.production.scripting.configuration import (
    scripting_configuration_from_snapshot,
)
from backend.src.production.scripting.exceptions import (
    ProductionPlanChecksumError,
    ProductionPlanContractError,
    ProductionPlanEncodingError,
    ProductionPlanIntegrityError,
    ProductionPlanJsonError,
    ProductionPlanMissingFileError,
    ProductionPlanNotFoundError,
    ProductionPlanPathError,
    ProductionPlanSizeError,
    ProductionPlanTransientReadError,
    ProductionPlanVersionError,
    ScriptingProviderAuthenticationError,
    ScriptingProviderConfigurationError,
    ScriptingProviderContractError,
    ScriptingProviderError,
    ScriptingProviderRateLimitError,
    ScriptingProviderResponseError,
    ScriptingProviderTimeoutError,
    ScriptingProviderUnavailableError,
)
from backend.src.production.scripting.models import validate_script_against_plan
from backend.src.production.scripting.ports import (
    ProductionPlanReader,
    ScriptingProvider,
    ScriptingProviderRequest,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder


class ScriptingHandler:
    supported_stages = frozenset({ProductionStage.SCRIPTING})

    def __init__(
        self,
        *,
        plan_reader: ProductionPlanReader,
        provider: ScriptingProvider,
        artifact_writer: ScriptingArtifactWriter,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        prompt_version: str = ScriptingPromptBuilder.scripting_prompt_version,
    ) -> None:
        self._reader = plan_reader
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
        if command.stage is not ProductionStage.SCRIPTING:
            raise ValueError("ScriptingHandler only supports SCRIPTING")
        started_at = self._aware_now()
        try:
            source = await self._reader.read_for_scripting(context=context)
            configuration = scripting_configuration_from_snapshot(
                command.configuration_snapshot
            )
            response = await self._provider.generate_script(
                ScriptingProviderRequest(
                    job_id=command.job_id,
                    command_id=command.command_id,
                    correlation_id=context.correlation_id,
                    attempt_number=command.attempt_number,
                    plan=source.plan,
                    configuration=configuration,
                    language=source.plan.language,
                    target_duration_seconds=source.plan.target_duration_seconds,
                )
            )
            script = validate_script_against_plan(response.script, source.plan)
            written = await self._writer.write_script(context=context, script=script)
        except (
            ProductionPlanTransientReadError,
            ScriptingProviderTimeoutError,
            ScriptingProviderRateLimitError,
            ScriptingProviderUnavailableError,
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
            ProductionPlanNotFoundError,
            ProductionPlanMissingFileError,
            ProductionPlanIntegrityError,
            ProductionPlanVersionError,
            ScriptingProviderConfigurationError,
            ScriptingProviderAuthenticationError,
            ValidationError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                error_code=self._error_code(exc),
            )
        except (
            ProductionPlanPathError,
            ProductionPlanSizeError,
            ProductionPlanEncodingError,
            ProductionPlanJsonError,
            ProductionPlanContractError,
            ScriptingProviderContractError,
            ScriptingProviderResponseError,
            ScriptingProviderError,
            ValueError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                error_code=self._error_code(exc),
            )

        metadata = {
            **response.metadata,
            "schema_version": script.schema_version,
            "source_plan_schema_version": script.source_plan_schema_version,
            "scripting_prompt_version": self._prompt_version,
            "source_plan_artifact_id": str(source.artifact_id),
            "source_plan_sha256": source.sha256,
            "requested_model": response.requested_model,
            "reported_model": response.reported_model,
            "model_mismatch": (
                response.reported_model is not None
                and response.requested_model is not None
                and response.reported_model != response.requested_model
            ),
            "request_id": response.request_id,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.total_tokens,
            "latency_ms": response.latency_ms,
            "scene_count": len(script.scenes),
        }
        public_metadata = {key: value for key, value in metadata.items() if value is not None}
        artifact = Artifact(
            artifact_id=self._uuid_factory(),
            job_id=command.job_id,
            artifact_type=ArtifactType.PRODUCTION_SCRIPT,
            relative_path=written.relative_path,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=written.size_bytes,
            sha256=written.sha256,
            provider=response.provider,
            model_version=response.model,
            metadata=public_metadata,
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
                "requested_model": response.requested_model,
                "reported_model": response.reported_model,
                "source_plan_artifact_id": str(source.artifact_id),
                "scene_count": len(script.scenes),
            },
        )
        return StageExecutionOutput(result=result, artifacts=(artifact,))

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        *,
        error_code: str,
        retry_after_seconds: float | None = None,
    ) -> StageExecutionOutput:
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=0,
                error_code=error_code,
                error_message="Scripting stage could not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={"handler": type(self).__name__, "error_category": error_code},
            )
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        mapping: tuple[tuple[type[Exception], str], ...] = (
            (ProductionPlanNotFoundError, "production_plan_not_found"),
            (ProductionPlanMissingFileError, "production_plan_missing_file"),
            (ProductionPlanChecksumError, "production_plan_checksum"),
            (ProductionPlanIntegrityError, "production_plan_integrity"),
            (ProductionPlanVersionError, "production_plan_version"),
            (ProductionPlanPathError, "production_plan_path"),
            (ProductionPlanSizeError, "production_plan_size"),
            (ProductionPlanEncodingError, "production_plan_encoding"),
            (ProductionPlanJsonError, "production_plan_json"),
            (ProductionPlanContractError, "production_plan_contract"),
            (ProductionPlanTransientReadError, "production_plan_read_transient"),
            (ScriptingProviderTimeoutError, "scripting_provider_timeout"),
            (ScriptingProviderRateLimitError, "scripting_provider_rate_limit"),
            (ScriptingProviderUnavailableError, "scripting_provider_unavailable"),
            (ScriptingProviderAuthenticationError, "scripting_provider_authentication"),
            (ScriptingProviderConfigurationError, "scripting_provider_configuration"),
            (ScriptingProviderContractError, "scripting_provider_contract"),
            (ScriptingProviderResponseError, "scripting_provider_response"),
            (ValidationError, "scripting_configuration_invalid"),
            (OSError, "scripting_artifact_write"),
        )
        return next(
            (code for error_type, code in mapping if isinstance(error, error_type)),
            "scripting_stage_error",
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("handler clock must return a timezone-aware datetime")
        return value


class ScriptHandler(SimulatedStageHandler):
    """Legacy isolated-runtime fixture; not used by Production composition."""

    supported_stages = frozenset({ProductionStage.SCRIPTING})
    artifact_type = ArtifactType.MANIFEST
    mime_type = "text/plain"
    extension = "txt"
