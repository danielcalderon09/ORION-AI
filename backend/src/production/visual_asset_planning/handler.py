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
from backend.src.production.planning.visual_strategy import VisualStrategyName
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.visual_asset_planning.artifact_writer import (
    VisualAssetPlanningArtifactWriter,
    WrittenShotExpansionArtifact,
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
    ShotExpansionDurationReader,
    VisualAssetPlanningProvider,
    VisualAssetPlanningProviderRequest,
    VisualAssetPlanningProviderResponse,
)
from backend.src.production.visual_asset_planning.prompt_builder import (
    VisualAssetPlanningPromptBuilder,
)
from backend.src.production.visual_asset_planning.shot_expansion import (
    PostTtsShotExpansion,
    build_post_tts_shot_expansion,
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
        duration_resolution_reader: ShotExpansionDurationReader | None = None,
        supported_provider_durations_seconds: tuple[int, ...] = (4, 6, 8),
        visual_strategy_name: VisualStrategyName = VisualStrategyName.FULL_VIDEO,
        prompt_version: str = (
            VisualAssetPlanningPromptBuilder.visual_asset_planning_prompt_version
        ),
    ) -> None:
        self._reader = scene_plan_reader
        self._provider = provider
        self._writer = artifact_writer
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._duration_reader = duration_resolution_reader
        self._supported_durations = tuple(
            sorted(set(supported_provider_durations_seconds))
        )
        self._visual_strategy_name = visual_strategy_name
        if not self._supported_durations or any(
            value <= 0 for value in self._supported_durations
        ):
            raise ValueError("supported provider durations must be positive")
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
            if existing is not None and self._is_historical_plan(
                existing.visual_asset_plan
            ):
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
                    expansion=None,
                    started_at=started_at,
                    response=None,
                    recovered=True,
                )
            expansion, written_expansion = await self._resolve_shot_expansion(
                command=command,
                context=context,
                source=source,
            )
            effective_scene_plan = (
                expansion.expanded_scene_plan
                if expansion is not None
                else source.scene_plan
            )
            if existing is not None:
                self._validate_expansion_provenance(existing.visual_asset_plan, written_expansion)
                plan = validate_visual_asset_plan_against_scene_plan(
                    existing.visual_asset_plan,
                    effective_scene_plan,
                    source_scene_plan_artifact_id=source.artifact_id,
                    source_scene_plan_sha256=source.sha256,
                )
                return self._success(
                    command=command,
                    source=source,
                    plan=plan,
                    written=existing,
                    expansion=written_expansion,
                    started_at=started_at,
                    response=None,
                    recovered=True,
                )
            request = VisualAssetPlanningProviderRequest(
                job_id=command.job_id,
                command_id=command.command_id,
                correlation_id=context.correlation_id,
                attempt_number=command.attempt_number,
                scene_plan=effective_scene_plan,
                configuration=configuration,
            )
            response = await self._provider.generate_visual_asset_plan(request)
            plan = response.visual_asset_plan.model_copy(
                update={
                    "source_scene_plan_artifact_id": source.artifact_id,
                    "source_scene_plan_sha256": source.sha256,
                    "source_shot_expansion_artifact_id": (
                        self._uuid_factory() if written_expansion is not None else None
                    ),
                    "source_shot_expansion_sha256": (
                        written_expansion.sha256 if written_expansion is not None else None
                    ),
                    "source_shot_expansion_fingerprint": (
                        written_expansion.shot_expansion.plan_fingerprint
                        if written_expansion is not None
                        else None
                    ),
                }
            )
            if plan.video_identity is None:
                plan = plan.model_copy(update={"video_identity": derive_video_identity(plan)})
            plan = validate_visual_asset_plan_against_scene_plan(
                plan,
                effective_scene_plan,
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
            expansion=written_expansion,
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
        expansion: WrittenShotExpansionArtifact | None,
        started_at: datetime,
        response: VisualAssetPlanningProviderResponse | None,
        recovered: bool,
    ) -> StageExecutionOutput:
        finished_at = self._aware_now()
        provider = response.provider if response is not None else "orion-recovery"
        model = response.model if response is not None else "existing-visual-asset-plan-v1"
        requested_model = response.requested_model if response is not None else None
        reported_model = response.reported_model if response is not None else None
        effective_scene_plan = (
            expansion.shot_expansion.expanded_scene_plan
            if expansion is not None
            else source.scene_plan
        )
        scene_count = len(effective_scene_plan.scenes)
        shot_count = sum(len(scene.shots) for scene in effective_scene_plan.scenes)
        artifact_id = self._uuid_factory()
        artifact_metadata: dict[str, object] = {
            "schema_version": plan.schema_version,
            "source_scene_plan_schema_version": (plan.source_scene_plan_schema_version),
            "source_scene_plan_artifact_id": str(source.artifact_id),
            "source_scene_plan_sha256": source.sha256,
            "source_shot_expansion_artifact_id": (
                str(plan.source_shot_expansion_artifact_id)
                if plan.source_shot_expansion_artifact_id is not None
                else None
            ),
            "source_shot_expansion_sha256": plan.source_shot_expansion_sha256,
            "source_shot_expansion_fingerprint": (
                plan.source_shot_expansion_fingerprint
            ),
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
        artifacts: tuple[Artifact, ...] = (artifact,)
        if expansion is not None:
            expansion_id = plan.source_shot_expansion_artifact_id
            if expansion_id is None:
                raise VisualAssetPlanningValidationException(
                    "expanded visual plan is missing shot expansion identity"
                )
            expansion_artifact = Artifact(
                artifact_id=expansion_id,
                job_id=command.job_id,
                artifact_type=ArtifactType.PRODUCTION_SHOT_EXPANSION,
                relative_path=expansion.relative_path,
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=expansion.size_bytes,
                sha256=expansion.sha256,
                provider="orion-local",
                model_version="post-tts-shot-expansion-v1",
                metadata={
                    "schema_version": expansion.shot_expansion.schema_version,
                    "plan_fingerprint": expansion.shot_expansion.plan_fingerprint,
                    "resolved_duration_ms": expansion.shot_expansion.resolved_duration_ms,
                    "shot_count": len(expansion.shot_expansion.allocations),
                },
            )
            artifacts = (expansion_artifact, artifact)
        result = StageResult(
            command_id=command.command_id,
            job_id=command.job_id,
            stage=command.stage,
            outcome=StageOutcome.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            progress_percent=100,
            output_artifact_ids=tuple(item.artifact_id for item in artifacts),
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
                "shot_expansion_fingerprint": (
                    expansion.shot_expansion.plan_fingerprint
                    if expansion is not None
                    else None
                ),
            },
        )
        return StageExecutionOutput(result=result, artifacts=artifacts)

    async def _resolve_shot_expansion(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadProductionScenePlan,
    ) -> tuple[PostTtsShotExpansion | None, WrittenShotExpansionArtifact | None]:
        if self._duration_reader is None:
            return None, None
        duration_source = await self._duration_reader.read_source_for_job(command.job_id)
        if duration_source is None:
            raise VisualAssetPlanningValidationException(
                "accepted narration duration must exist before final visual planning"
            )
        expected = build_post_tts_shot_expansion(
            job_id=command.job_id,
            source_scene_plan_artifact_id=source.artifact_id,
            source_scene_plan_sha256=source.sha256,
            source_duration_artifact_id=duration_source.artifact_id,
            source_duration_sha256=duration_source.sha256,
            scene_plan=source.scene_plan,
            duration_resolution=duration_source.resolution,
            supported_provider_durations_seconds=self._supported_durations,
            visual_strategy_name=self._visual_strategy_name,
        )
        existing = await self._writer.read_existing_shot_expansion(context=context)
        if existing is not None:
            if existing.shot_expansion != expected:
                raise VisualAssetPlanningValidationException(
                    "durable shot expansion differs from current narration or scene plan"
                )
            return expected, existing
        written = await self._writer.write_shot_expansion(
            context=context,
            shot_expansion=expected,
        )
        return expected, written

    @staticmethod
    def _validate_expansion_provenance(
        plan: ProductionVisualAssetPlan,
        expansion: WrittenShotExpansionArtifact | None,
    ) -> None:
        if expansion is None:
            return
        if (
            plan.source_shot_expansion_sha256 != expansion.sha256
            or plan.source_shot_expansion_fingerprint
            != expansion.shot_expansion.plan_fingerprint
            or plan.source_shot_expansion_artifact_id is None
        ):
            raise VisualAssetPlanningValidationException(
                "visual asset plan differs from durable shot expansion"
            )

    @staticmethod
    def _is_historical_plan(plan: ProductionVisualAssetPlan) -> bool:
        return (
            plan.source_shot_expansion_artifact_id is None
            and plan.source_shot_expansion_sha256 is None
            and plan.source_shot_expansion_fingerprint is None
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
