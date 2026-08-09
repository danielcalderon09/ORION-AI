"""Durable provider-driven VISUAL_ASSET_PLANNING handler."""

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
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.visual_asset_planning.artifact_writer import (
    VisualAssetPlanningArtifactWriter,
    WrittenVisualAssetPlanningArtifact,
)
from backend.src.production.visual_asset_planning.configuration import (
    visual_asset_planning_configuration_from_snapshot,
)
from backend.src.production.visual_asset_planning.exceptions import (
    ProductionScenePlanReadException,
    ProductionScenePlanTransientReadException,
    VisualAssetPlanningProviderAuthenticationException,
    VisualAssetPlanningProviderContractException,
    VisualAssetPlanningProviderException,
    VisualAssetPlanningProviderRateLimitException,
    VisualAssetPlanningProviderTimeoutException,
    VisualAssetPlanningProviderUnavailableException,
    VisualAssetPlanningValidationException,
)
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
    derive_video_identity,
    validate_visual_asset_plan_against_scene_plan,
)
from backend.src.production.visual_asset_planning.ports import (
    ProductionScenePlanReader,
    ReadProductionScenePlan,
    VisualAssetPlanningProvider,
    VisualAssetPlanningProviderRequest,
    VisualAssetPlanningProviderResponse,
)
from backend.src.production.visual_asset_planning.prompt_builder import (
    VisualAssetPlanningPromptBuilder,
)


class VisualAssetPlanningHandler:
    supported_stages = frozenset({ProductionStage.VISUAL_ASSET_PLANNING})

    def __init__(
        self,
        *,
        scene_plan_reader: ProductionScenePlanReader,
        provider: VisualAssetPlanningProvider,
        artifact_writer: VisualAssetPlanningArtifactWriter,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        prompt_version: str = (
            VisualAssetPlanningPromptBuilder.visual_asset_planning_prompt_version
        ),
    ) -> None:
        self._reader = scene_plan_reader
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
        if command.stage is not ProductionStage.VISUAL_ASSET_PLANNING:
            raise ValueError("VisualAssetPlanningHandler only supports VISUAL_ASSET_PLANNING")
        started_at = self._aware_now()
        try:
            source = await self._reader.read_for_visual_asset_planning(context=context)
            configuration = visual_asset_planning_configuration_from_snapshot(
                command.configuration_snapshot
            )
            existing = await self._writer.read_existing(context=context)
            if existing is not None:
                plan = validate_visual_asset_plan_against_scene_plan(
                    existing.visual_asset_plan,
                    source.scene_plan,
                    source_scene_plan_artifact_id=source.artifact_id,
                    source_scene_plan_sha256=source.sha256,
                )
                return self._success(
                    command=command,
                    source=source,
                    plan=plan,
                    written=existing,
                    started_at=started_at,
                    response=None,
                    recovered=True,
                )
            request = VisualAssetPlanningProviderRequest(
                job_id=command.job_id,
                command_id=command.command_id,
                correlation_id=context.correlation_id,
                attempt_number=command.attempt_number,
                scene_plan=source.scene_plan,
                configuration=configuration,
            )
            response = await self._provider.generate_visual_asset_plan(request)
            plan = response.visual_asset_plan.model_copy(
                update={
                    "source_scene_plan_artifact_id": source.artifact_id,
                    "source_scene_plan_sha256": source.sha256,
                }
            )
            if plan.video_identity is None:
                plan = plan.model_copy(update={"video_identity": derive_video_identity(plan)})
            plan = validate_visual_asset_plan_against_scene_plan(
                plan,
                source.scene_plan,
                source_scene_plan_artifact_id=source.artifact_id,
                source_scene_plan_sha256=source.sha256,
            )
            written = await self._writer.write_visual_asset_plan(
                context=context,
                visual_asset_plan=plan,
            )
        except (
            ProductionScenePlanTransientReadException,
            VisualAssetPlanningProviderTimeoutException,
            VisualAssetPlanningProviderRateLimitException,
            VisualAssetPlanningProviderUnavailableException,
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
            ProductionScenePlanReadException,
            VisualAssetPlanningValidationException,
            VisualAssetPlanningProviderAuthenticationException,
            VisualAssetPlanningProviderContractException,
            VisualAssetPlanningProviderException,
            ValidationError,
            TypeError,
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
            plan=plan,
            written=written,
            started_at=started_at,
            response=response,
            recovered=False,
        )

    def _success(
        self,
        *,
        command: StageCommand,
        source: ReadProductionScenePlan,
        plan: ProductionVisualAssetPlan,
        written: WrittenVisualAssetPlanningArtifact,
        started_at: datetime,
        response: VisualAssetPlanningProviderResponse | None,
        recovered: bool,
    ) -> StageExecutionOutput:
        finished_at = self._aware_now()
        provider = response.provider if response is not None else "orion-recovery"
        model = response.model if response is not None else "existing-visual-asset-plan-v1"
        requested_model = response.requested_model if response is not None else None
        reported_model = response.reported_model if response is not None else None
        scene_count = len(source.scene_plan.scenes)
        shot_count = sum(len(scene.shots) for scene in source.scene_plan.scenes)
        artifact_id = self._uuid_factory()
        artifact_metadata: dict[str, object] = {
            "schema_version": plan.schema_version,
            "source_scene_plan_schema_version": (plan.source_scene_plan_schema_version),
            "source_scene_plan_artifact_id": str(source.artifact_id),
            "source_scene_plan_sha256": source.sha256,
            "visual_asset_planning_prompt_version": self._prompt_version,
            "asset_count": len(plan.assets),
            "scene_count": scene_count,
            "shot_count": shot_count,
            "provider": provider,
            "model": model,
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
                "deterministic": response.metadata.get("deterministic"),
                "simulated": response.metadata.get("simulated"),
            }
            artifact_metadata.update(
                {key: value for key, value in optional.items() if value is not None}
            )
        artifact = Artifact(
            artifact_id=artifact_id,
            job_id=command.job_id,
            artifact_type=ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN,
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
                "source_scene_plan_artifact_id": str(source.artifact_id),
                "asset_count": len(plan.assets),
                "scene_count": scene_count,
                "shot_count": shot_count,
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
                error_message="Visual asset planning stage could not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "error_category": error_code,
                },
            )
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        mapping: tuple[tuple[type[Exception], str], ...] = (
            (
                ProductionScenePlanTransientReadException,
                "production_scene_plan_read_transient",
            ),
            (ProductionScenePlanReadException, "production_scene_plan_invalid"),
            (
                VisualAssetPlanningProviderTimeoutException,
                "visual_asset_planning_provider_timeout",
            ),
            (
                VisualAssetPlanningProviderRateLimitException,
                "visual_asset_planning_provider_rate_limit",
            ),
            (
                VisualAssetPlanningProviderUnavailableException,
                "visual_asset_planning_provider_unavailable",
            ),
            (
                VisualAssetPlanningProviderAuthenticationException,
                "visual_asset_planning_provider_authentication",
            ),
            (
                VisualAssetPlanningProviderContractException,
                "visual_asset_planning_provider_contract",
            ),
            (
                VisualAssetPlanningValidationException,
                "visual_asset_planning_validation",
            ),
            (OSError, "visual_asset_planning_artifact_write"),
        )
        return next(
            (code for error_type, code in mapping if isinstance(error, error_type)),
            "visual_asset_planning_stage_error",
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("handler clock must return a timezone-aware datetime")
        return value
