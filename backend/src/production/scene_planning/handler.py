"""Durable provider-driven SCENE_PLANNING handler."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType, ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.scene_planning.artifact_writer import (
    ScenePlanningArtifactWriter,
    WrittenScenePlanningArtifact,
)
from backend.src.production.scene_planning.exceptions import (
    ProductionScriptContractException,
    ProductionScriptEncodingException,
    ProductionScriptIntegrityException,
    ProductionScriptJsonException,
    ProductionScriptMissingFileException,
    ProductionScriptNotFoundException,
    ProductionScriptPathException,
    ProductionScriptSizeException,
    ProductionScriptTransientReadException,
    ProductionScriptVersionException,
    ScenePlanningProviderAuthenticationException,
    ScenePlanningProviderConfigurationException,
    ScenePlanningProviderContractException,
    ScenePlanningProviderException,
    ScenePlanningProviderRateLimitException,
    ScenePlanningProviderResponseException,
    ScenePlanningProviderTimeoutException,
    ScenePlanningProviderUnavailableException,
    ScenePlanningValidationException,
)
from backend.src.production.scene_planning.models import (
    ProductionScenePlan,
    validate_scene_plan_against_script,
)
from backend.src.production.scene_planning.ports import (
    ProductionScriptReader,
    ReadProductionScript,
    ScenePlanningProvider,
    ScenePlanningProviderResponse,
)
from backend.src.production.scene_planning.prompt_builder import ScenePlanningPromptBuilder


class ScenePlanningHandler:
    supported_stages = frozenset({ProductionStage.SCENE_PLANNING})

    def __init__(
        self,
        *,
        script_reader: ProductionScriptReader,
        provider: ScenePlanningProvider,
        artifact_writer: ScenePlanningArtifactWriter,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        prompt_version: str = ScenePlanningPromptBuilder.scene_planning_prompt_version,
    ) -> None:
        self._reader = script_reader
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
        if command.stage is not ProductionStage.SCENE_PLANNING:
            raise ValueError("ScenePlanningHandler only supports SCENE_PLANNING")
        started_at = self._aware_now()
        try:
            source = await self._reader.read_for_scene_planning(context=context)
            existing = await self._writer.read_existing(context=context)
            if existing is not None:
                scene_plan = validate_scene_plan_against_script(
                    existing.scene_plan,
                    source.script,
                    source_script_sha256=source.sha256,
                )
                return self._success(
                    command=command,
                    source=source,
                    scene_plan=scene_plan,
                    written=existing,
                    started_at=started_at,
                    response=None,
                    recovered=True,
                )
            response = await self._provider.generate_scene_plan(source.script)
            scene_plan = response.scene_plan.model_copy(
                update={
                    "source_script_sha256": source.sha256,
                    "scenes": tuple(
                        scene.model_copy(
                            update={
                                "story_beat": scene.story_beat
                                or source.script.scenes[index].story_beat
                            }
                        )
                        for index, scene in enumerate(response.scene_plan.scenes)
                    ),
                }
            )
            scene_plan = validate_scene_plan_against_script(
                scene_plan,
                source.script,
                source_script_sha256=source.sha256,
            )
            written = await self._writer.write_scene_plan(
                context=context,
                scene_plan=scene_plan,
            )
        except (
            ProductionScriptTransientReadException,
            ScenePlanningProviderTimeoutException,
            ScenePlanningProviderRateLimitException,
            ScenePlanningProviderUnavailableException,
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
            ProductionScriptNotFoundException,
            ProductionScriptMissingFileException,
            ProductionScriptIntegrityException,
            ProductionScriptVersionException,
            ScenePlanningProviderConfigurationException,
            ScenePlanningProviderAuthenticationException,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                error_code=self._error_code(exc),
            )
        except (
            ProductionScriptPathException,
            ProductionScriptSizeException,
            ProductionScriptEncodingException,
            ProductionScriptJsonException,
            ProductionScriptContractException,
            ScenePlanningValidationException,
            ScenePlanningProviderContractException,
            ScenePlanningProviderResponseException,
            ScenePlanningProviderException,
            ValueError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                error_code=self._error_code(exc),
            )
        return self._success(
            command=command,
            source=source,
            scene_plan=scene_plan,
            written=written,
            started_at=started_at,
            response=response,
            recovered=False,
        )

    def _success(
        self,
        *,
        command: StageCommand,
        source: ReadProductionScript,
        scene_plan: ProductionScenePlan,
        written: WrittenScenePlanningArtifact,
        started_at: datetime,
        response: ScenePlanningProviderResponse | None,
        recovered: bool,
    ) -> StageExecutionOutput:
        finished_at = self._aware_now()
        provider = response.provider if response is not None else "orion-recovery"
        model = response.model if response is not None else "existing-scene-plan-v1"
        requested_model = response.requested_model if response is not None else None
        reported_model = response.reported_model if response is not None else None
        artifact_id = self._uuid_factory()
        artifact_metadata: dict[str, object] = {
            "schema_version": scene_plan.schema_version,
            "source_script_schema_version": scene_plan.source_script_schema_version,
            "scene_planning_prompt_version": self._prompt_version,
            "source_script_artifact_id": str(source.artifact_id),
            "source_script_sha256": source.sha256,
            "scene_count": len(scene_plan.scenes),
            "shot_count": sum(len(scene.shots) for scene in scene_plan.scenes),
            "recovered": recovered,
            "handler_duration_ms": max(
                0.0,
                (finished_at - started_at).total_seconds() * 1000,
            ),
        }
        if response is not None:
            optional = {
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
                "provider_latency_ms": response.latency_ms,
                "finish_reason": response.finish_reason,
            }
            artifact_metadata.update(
                {key: value for key, value in optional.items() if value is not None}
            )
        artifact = Artifact(
            artifact_id=artifact_id,
            job_id=command.job_id,
            artifact_type=ArtifactType.PRODUCTION_SCENE_PLAN,
            relative_path=written.relative_path,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=written.size_bytes,
            sha256=written.sha256,
            provider=provider,
            model_version=model,
            metadata=artifact_metadata,
        )
        result = StageResult(
            command_id=command.command_id,
            job_id=command.job_id,
            stage=command.stage,
            outcome=StageOutcome.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            progress_percent=100,
            output_artifact_ids=(artifact_id,),
            metadata={
                "handler": type(self).__name__,
                "provider": provider,
                "model": model,
                "requested_model": requested_model,
                "reported_model": reported_model,
                "source_script_artifact_id": str(source.artifact_id),
                "scene_count": len(scene_plan.scenes),
                "shot_count": sum(len(scene.shots) for scene in scene_plan.scenes),
                "recovered": recovered,
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
                error_message="Scene-planning stage could not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={"handler": type(self).__name__, "error_category": error_code},
            )
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        mapping: tuple[tuple[type[Exception], str], ...] = (
            (ProductionScriptNotFoundException, "production_script_not_found"),
            (ProductionScriptMissingFileException, "production_script_missing_file"),
            (ProductionScriptIntegrityException, "production_script_integrity"),
            (ProductionScriptVersionException, "production_script_version"),
            (ProductionScriptPathException, "production_script_path"),
            (ProductionScriptSizeException, "production_script_size"),
            (ProductionScriptEncodingException, "production_script_encoding"),
            (ProductionScriptJsonException, "production_script_json"),
            (ProductionScriptContractException, "production_script_contract"),
            (ProductionScriptTransientReadException, "production_script_read_transient"),
            (ScenePlanningProviderTimeoutException, "scene_planning_provider_timeout"),
            (ScenePlanningProviderRateLimitException, "scene_planning_provider_rate_limit"),
            (ScenePlanningProviderUnavailableException, "scene_planning_provider_unavailable"),
            (
                ScenePlanningProviderAuthenticationException,
                "scene_planning_provider_authentication",
            ),
            (
                ScenePlanningProviderConfigurationException,
                "scene_planning_provider_configuration",
            ),
            (ScenePlanningProviderContractException, "scene_planning_provider_contract"),
            (ScenePlanningProviderResponseException, "scene_planning_provider_response"),
            (ScenePlanningValidationException, "scene_planning_validation"),
            (OSError, "scene_planning_artifact_write"),
        )
        return next(
            (code for error_type, code in mapping if isinstance(error, error_type)),
            "scene_planning_stage_error",
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("handler clock must return a timezone-aware datetime")
        return value
