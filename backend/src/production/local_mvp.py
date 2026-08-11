"""Focused application service for ORION's provider-configured local MVP."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.events import (
    ProductionStageStarted,
    ProductionStageSucceeded,
)
from backend.src.production.application.services.models import CreateProductionJobCommand
from backend.src.production.application.services.production_jobs import (
    CreateProductionJobService,
    GetProductionJobService,
    ListProductionArtifactsService,
    ListProductionEventsService,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.cost_accounting import (
    JobCostCategory,
    ProductionJobCostSummary,
    derive_durable_job_cost_summary,
)
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionJobStatus,
    ProductionStage,
)
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.image_acquisition.hybrid_acquisition import (
    deserialize_hybrid_acquisition_manifest,
)
from backend.src.production.planning.aggregate_visual_budget import (
    deserialize_aggregate_visual_budget_plan,
)
from backend.src.production.planning.visual_strategy import (
    deserialize_hybrid_visual_strategy_plan,
)
from backend.src.production.render_validation.models import FinalValidationResult
from backend.src.production.render_validation.serialization import (
    deserialize_final_render_validation,
)
from backend.src.production.runtime.worker import ProductionWorker

LOCAL_MVP_MODE: Literal["local_simulated_e2e"] = "local_simulated_e2e"
LOCAL_MVP_PROFILE_VERSION = "1.0.0"
DEFAULT_MAX_STAGE_ITERATIONS = 50


class LocalMvpRequest(ContractModel):
    mode: Literal["local_simulated_e2e"] = LOCAL_MVP_MODE
    prompt: str | None = Field(default=None, max_length=10_000)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    target_duration_seconds: int = Field(default=8, ge=4, le=60)
    scene_count_hint: int = Field(default=2, ge=1, le=50)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    resume_job_id: UUID | None = None

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str | None) -> str | None:
        normalized = " ".join((value or "").split())
        return normalized or None

    @model_validator(mode="after")
    def require_new_prompt_or_resume(self) -> LocalMvpRequest:
        if self.resume_job_id is None and self.prompt is None:
            raise ValueError("prompt must not be empty for a new local MVP job")
        if self.resume_job_id is not None and self.prompt is not None:
            raise ValueError("prompt cannot be combined with resume_job_id")
        return self


class LocalMvpProgress(ContractModel):
    stage: ProductionStage
    attempt_number: int = Field(ge=1)
    outcome: str = Field(min_length=1, max_length=80)
    progress_percent: float = Field(ge=0, le=100)
    artifacts_emitted: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=200)


class LocalMvpOutput(ContractModel):
    render_artifact_id: UUID
    validation_artifact_id: UUID
    workspace_relative_path: str
    local_absolute_path: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_ms: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate_numerator: int = Field(gt=0)
    frame_rate_denominator: int = Field(gt=0)
    video_codec: str
    audio_codec: str | None = None


class LocalMvpFailure(ContractModel):
    error_code: str
    retryable: bool
    most_recent_attempt: int | None = Field(default=None, ge=1)
    completed_artifact_count: int = Field(ge=0)
    recommended_action: str


class LocalMvpVisualSummary(ContractModel):
    visual_strategy: str
    visual_shots: int = Field(ge=1)
    generated_video_shots: int = Field(ge=0)
    generated_image_shots: int = Field(ge=0)
    reused_video_shots: int = Field(ge=0)
    reused_image_shots: int = Field(ge=0)
    image_requests: int = Field(ge=0)
    video_requests: int = Field(ge=0)
    purchased_video_seconds: int = Field(ge=0)
    estimated_visual_cost_usd: str
    image_estimated_cost_usd: str = "0"
    image_reported_cost_usd: str = "0"
    image_accounted_cost_usd: str = "0"
    image_reported_cost_request_count: int = Field(default=0, ge=0)
    image_estimated_fallback_request_count: int = Field(default=0, ge=0)


class LocalMvpCostSummary(ContractModel):
    scripting_accounted_cost_usd: str
    tts_accounted_cost_usd: str
    fitting_accounted_cost_usd: str
    image_accounted_cost_usd: str
    video_accounted_cost_usd: str
    total_accounted_cost_usd: str
    total_reported_cost_usd: str
    total_estimated_cost_usd: str
    reported_cost_coverage_percent: str
    fully_reported: bool
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class LocalMvpReport(ContractModel):
    mode: Literal["local_simulated_e2e"] = LOCAL_MVP_MODE
    job_id: UUID
    status: ProductionJobStatus
    final_stage: ProductionStage
    success: bool
    resumed: bool
    stage_iterations: int = Field(ge=0)
    stages_succeeded: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    progress: tuple[LocalMvpProgress, ...] = ()
    output: LocalMvpOutput | None = None
    failure: LocalMvpFailure | None = None
    visual_summary: LocalMvpVisualSummary | None = None
    cost_summary: LocalMvpCostSummary | None = None


class LocalMvpApplication:
    """Create or resume one job and drive the existing canonical worker."""

    def __init__(
        self,
        *,
        create_job: CreateProductionJobService,
        get_job: GetProductionJobService,
        list_artifacts: ListProductionArtifactsService,
        list_events: ListProductionEventsService,
        worker: ProductionWorker,
        workspace_root: Path,
        configured_renderer: str,
        max_validation_manifest_bytes: int = 4_000_000,
    ) -> None:
        if configured_renderer != "ffmpeg":
            raise ValueError("local_simulated_e2e requires the ffmpeg renderer")
        self._create_job = create_job
        self._get_job = get_job
        self._list_artifacts = list_artifacts
        self._list_events = list_events
        self._worker = worker
        self._workspace = WorkspaceConfinement(workspace_root)
        self._manifest_limit = max_validation_manifest_bytes

    async def run(
        self,
        request: LocalMvpRequest,
        *,
        max_stage_iterations: int = DEFAULT_MAX_STAGE_ITERATIONS,
        progress_callback: Callable[[LocalMvpProgress], None] | None = None,
    ) -> LocalMvpReport:
        if max_stage_iterations < 1:
            raise ValueError("max_stage_iterations must be positive")
        started = time.perf_counter()
        resumed = request.resume_job_id is not None
        if resumed:
            assert request.resume_job_id is not None
            view = await self._get_job.execute(request.resume_job_id)
        else:
            assert request.prompt is not None
            profile = local_mvp_profile(
                target_duration_seconds=request.target_duration_seconds,
                aspect_ratio=request.aspect_ratio,
                scene_count_hint=request.scene_count_hint,
            )
            view = await self._create_job.execute(
                CreateProductionJobCommand(
                    prompt=request.prompt,
                    configuration=profile,
                    generate_clips_after_render=False,
                    client_request_id=_client_request_id(request, profile),
                    metadata={
                        "mode": LOCAL_MVP_MODE,
                        "profile_version": LOCAL_MVP_PROFILE_VERSION,
                        "project_id": request.project_id,
                        "title": request.title,
                    },
                )
            )

        if view.job.status is ProductionJobStatus.COMPLETED:
            return await self._completed_report(
                view.job,
                resumed=resumed,
                iterations=0,
                progress=(),
                started=started,
            )
        if view.job.status in _STOPPED_STATUSES:
            return await self._failure_report(
                view.job,
                resumed=resumed,
                iterations=0,
                progress=(),
                started=started,
            )

        progress_items: list[LocalMvpProgress] = []
        for iteration in range(1, max_stage_iterations + 1):
            before = (await self._get_job.execute(view.job.job_id)).job
            before_artifacts = await self._list_artifacts.execute(before.job_id)
            worker_result = await self._worker.run_once()
            view = await self._get_job.execute(before.job_id)
            current = view.job
            if worker_result.processed and worker_result.job_id == current.job_id:
                after_artifacts = await self._list_artifacts.execute(current.job_id)
                stage = before.current_stage
                if before.status is ProductionJobStatus.QUEUED:
                    stage = current.current_stage
                item = LocalMvpProgress(
                    stage=stage,
                    attempt_number=await self._latest_attempt(current.job_id, stage),
                    outcome=_progress_outcome(before, current),
                    progress_percent=(
                        100.0
                        if before.status is ProductionJobStatus.RUNNING
                        and current.status not in _UNSUCCESSFUL_STATUSES
                        else 0.0
                    ),
                    artifacts_emitted=max(
                        0,
                        len(after_artifacts.items) - len(before_artifacts.items),
                    ),
                    error_code=current.error_code,
                )
                progress_items.append(item)
                if progress_callback is not None:
                    progress_callback(item)

            if current.status is ProductionJobStatus.COMPLETED:
                return await self._completed_report(
                    current,
                    resumed=resumed,
                    iterations=iteration,
                    progress=tuple(progress_items),
                    started=started,
                )
            if current.status in _STOPPED_STATUSES:
                return await self._failure_report(
                    current,
                    resumed=resumed,
                    iterations=iteration,
                    progress=tuple(progress_items),
                    started=started,
                )
            if not worker_result.processed:
                return await self._failure_report(
                    current.model_copy(
                        update={
                            "error_code": "local_worker_no_progress",
                            "error_message": "No local worker could claim the runnable job",
                        }
                    ),
                    resumed=resumed,
                    iterations=iteration,
                    progress=tuple(progress_items),
                    started=started,
                )

        current = (await self._get_job.execute(view.job.job_id)).job
        return await self._failure_report(
            current.model_copy(
                update={
                    "error_code": "local_iteration_limit_reached",
                    "error_message": "The bounded local orchestration loop reached its limit",
                }
            ),
            resumed=resumed,
            iterations=max_stage_iterations,
            progress=tuple(progress_items),
            started=started,
        )

    async def _completed_report(
        self,
        job: ProductionJob,
        *,
        resumed: bool,
        iterations: int,
        progress: tuple[LocalMvpProgress, ...],
        started: float,
    ) -> LocalMvpReport:
        events = await self._list_events.execute(job.job_id)
        try:
            output = await self._load_output(job)
        except (OSError, RuntimeError, ValueError) as exc:
            return LocalMvpReport(
                job_id=job.job_id,
                status=job.status,
                final_stage=job.current_stage,
                success=False,
                resumed=resumed,
                stage_iterations=iterations,
                stages_succeeded=_succeeded_stage_count(events.items),
                elapsed_seconds=time.perf_counter() - started,
                progress=progress,
                failure=LocalMvpFailure(
                    error_code="completed_output_inconsistent",
                    retryable=False,
                    most_recent_attempt=await self._latest_attempt(
                        job.job_id, ProductionStage.VALIDATING_RENDER
                    ),
                    completed_artifact_count=len(
                        (await self._list_artifacts.execute(job.job_id)).items
                    ),
                    recommended_action=f"Inspect durable final validation: {str(exc)[:200]}",
                ),
                cost_summary=self._load_cost_summary(job.job_id),
            )
        return LocalMvpReport(
            job_id=job.job_id,
            status=job.status,
            final_stage=job.current_stage,
            success=True,
            resumed=resumed,
            stage_iterations=iterations,
            stages_succeeded=_succeeded_stage_count(events.items),
            elapsed_seconds=time.perf_counter() - started,
            progress=progress,
            output=output,
            visual_summary=await self._load_visual_summary(job.job_id),
            cost_summary=self._load_cost_summary(job.job_id),
        )

    def _load_cost_summary(self, job_id: UUID) -> LocalMvpCostSummary:
        job_root = self._workspace.resolve(
            f"production/{job_id}", require_exists=False
        )
        summary = derive_durable_job_cost_summary(job_id=job_id, job_root=job_root)
        return _local_cost_summary(summary)

    async def _load_visual_summary(self, job_id: UUID) -> LocalMvpVisualSummary | None:
        page = await self._list_artifacts.execute(job_id)
        budgets = tuple(
            item.artifact
            for item in page.items
            if item.artifact.artifact_type is ArtifactType.AGGREGATE_VISUAL_BUDGET_PLAN
        )
        strategies = tuple(
            item.artifact
            for item in page.items
            if item.artifact.artifact_type is ArtifactType.HYBRID_VISUAL_STRATEGY_PLAN
        )
        if not budgets or not strategies:
            return None
        budget_artifact = max(budgets, key=lambda item: item.relative_path)
        strategy_artifact = max(strategies, key=lambda item: item.relative_path)
        acquisitions = tuple(
            item.artifact
            for item in page.items
            if item.artifact.artifact_type is ArtifactType.HYBRID_ASSET_ACQUISITION_MANIFEST
        )
        budget = deserialize_aggregate_visual_budget_plan(
            self._workspace.resolve(
                budget_artifact.relative_path, require_exists=True
            ).read_bytes()
        )
        strategy = deserialize_hybrid_visual_strategy_plan(
            self._workspace.resolve(
                strategy_artifact.relative_path, require_exists=True
            ).read_bytes()
        )
        accounting = None
        if acquisitions:
            acquisition = deserialize_hybrid_acquisition_manifest(
                self._workspace.resolve(
                    max(acquisitions, key=lambda item: item.relative_path).relative_path,
                    require_exists=True,
                ).read_bytes()
            )
            accounting = acquisition.accounting
        return LocalMvpVisualSummary(
            visual_strategy=strategy.strategy_name.value,
            visual_shots=strategy.summary.visual_shot_count,
            generated_video_shots=strategy.summary.generated_video_shots,
            generated_image_shots=strategy.summary.generated_image_shots,
            reused_video_shots=strategy.summary.reused_video_shots,
            reused_image_shots=strategy.summary.reused_image_shots,
            image_requests=budget.image_requests,
            video_requests=budget.video_requests,
            purchased_video_seconds=budget.purchased_video_seconds,
            estimated_visual_cost_usd=str(budget.estimated_total_visual_cost_usd),
            image_estimated_cost_usd=str(
                accounting.estimated_image_cost_usd if accounting else budget.estimated_image_cost_usd
            ),
            image_reported_cost_usd=str(
                accounting.reported_image_cost_usd if accounting else 0
            ),
            image_accounted_cost_usd=str(
                accounting.accounted_image_cost_usd if accounting else budget.estimated_image_cost_usd
            ),
            image_reported_cost_request_count=(
                accounting.reported_cost_request_count if accounting else 0
            ),
            image_estimated_fallback_request_count=(
                accounting.estimated_fallback_request_count if accounting else 0
            ),
        )

    async def _failure_report(
        self,
        job: ProductionJob,
        *,
        resumed: bool,
        iterations: int,
        progress: tuple[LocalMvpProgress, ...],
        started: float,
    ) -> LocalMvpReport:
        artifacts = await self._list_artifacts.execute(job.job_id)
        events = await self._list_events.execute(job.job_id)
        code = job.error_code or f"pipeline_stopped_{job.status.value}"
        return LocalMvpReport(
            job_id=job.job_id,
            status=job.status,
            final_stage=job.current_stage,
            success=False,
            resumed=resumed,
            stage_iterations=iterations,
            stages_succeeded=_succeeded_stage_count(events.items),
            elapsed_seconds=time.perf_counter() - started,
            progress=progress,
            failure=LocalMvpFailure(
                error_code=code,
                retryable=job.status is ProductionJobStatus.WAITING_FOR_RETRY,
                most_recent_attempt=await self._latest_attempt(job.job_id, job.current_stage),
                completed_artifact_count=len(artifacts.items),
                recommended_action=_recommended_action(job.status, code),
            ),
            cost_summary=self._load_cost_summary(job.job_id),
        )

    async def _load_output(self, job: ProductionJob) -> LocalMvpOutput:
        page = await self._list_artifacts.execute(job.job_id)
        artifacts = tuple(item.artifact for item in page.items)
        validations = tuple(
            item
            for item in artifacts
            if item.artifact_type is ArtifactType.FINAL_RENDER_VALIDATION
            and item.status is ArtifactStatus.READY
            and item.metadata.get("validation_result") == FinalValidationResult.PASSED.value
        )
        if not validations:
            raise RuntimeError("FINAL_RENDER_VALIDATION is missing")
        validation = max(validations, key=lambda item: item.relative_path)
        validation_path = self._workspace.resolve(
            validation.relative_path,
            require_exists=True,
        )
        self._workspace.reject_unsafe_file(validation_path)
        if validation_path.stat().st_size > self._manifest_limit:
            raise RuntimeError("FINAL_RENDER_VALIDATION exceeds its configured limit")
        content = validation_path.read_bytes()
        if validation.sha256 != hashlib.sha256(content).hexdigest():
            raise RuntimeError("FINAL_RENDER_VALIDATION checksum differs")
        manifest = deserialize_final_render_validation(content)
        if manifest.validation_result is not FinalValidationResult.PASSED:
            raise RuntimeError("final validation did not pass")
        if manifest.render_artifact_id is None or manifest.ffprobe_summary is None:
            raise RuntimeError("final validation is incomplete")
        if job.long_form_artifact_id not in {None, manifest.render_artifact_id}:
            raise RuntimeError("job and final validation reference different renders")
        renders = tuple(
            item
            for item in artifacts
            if item.artifact_type is ArtifactType.LONG_FORM_RENDER
            and item.artifact_id == manifest.render_artifact_id
            and item.status is ArtifactStatus.READY
        )
        if len(renders) != 1:
            raise RuntimeError("validated LONG_FORM_RENDER is missing or conflicting")
        render = renders[0]
        render_path = self._workspace.resolve(render.relative_path, require_exists=True)
        self._workspace.reject_unsafe_file(render_path)
        media_size, media_sha256 = _file_identity(render_path)
        if render.size_bytes != media_size or render.sha256 != media_sha256:
            raise RuntimeError("LONG_FORM_RENDER integrity differs")
        if manifest.render_checksum != media_sha256 or manifest.render_size_bytes != media_size:
            raise RuntimeError("final validation and render integrity differ")
        summary = manifest.ffprobe_summary
        return LocalMvpOutput(
            render_artifact_id=render.artifact_id,
            validation_artifact_id=validation.artifact_id,
            workspace_relative_path=render.relative_path,
            local_absolute_path=str(render_path),
            size_bytes=media_size,
            sha256=media_sha256,
            duration_ms=summary.duration_ms,
            width=summary.width,
            height=summary.height,
            frame_rate_numerator=summary.frame_rate_numerator,
            frame_rate_denominator=summary.frame_rate_denominator,
            video_codec=summary.video_codec,
            audio_codec=summary.audio_codec,
        )

    async def _latest_attempt(self, job_id: UUID, stage: ProductionStage) -> int:
        events = await self._list_events.execute(job_id)
        attempts = tuple(
            item.attempt_number
            for item in events.items
            if isinstance(item, ProductionStageStarted) and item.stage is stage
        )
        return max(attempts, default=1)


def local_mvp_profile(
    *,
    target_duration_seconds: int = 8,
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16",
    scene_count_hint: int = 2,
) -> dict[str, object]:
    if not 4 <= target_duration_seconds <= 60:
        raise ValueError("target duration must be between 4 and 60 seconds")
    if not 1 <= scene_count_hint <= 50:
        raise ValueError("scene count must be between 1 and 50")
    dimensions = {
        "9:16": (576, 1024),
        "16:9": (1024, 576),
        "1:1": (1024, 1024),
    }
    width, height = dimensions[aspect_ratio]
    return {
        "planning": {
            "language": "es",
            "target_duration_seconds": target_duration_seconds,
            "aspect_ratio": aspect_ratio,
            "visual_style": "cinematic",
            "narrative_style": "engaging",
            "scene_count_hint": scene_count_hint,
        },
        "visual_asset_planning": {
            "preferred_asset_kind": "still_image",
            "images_per_shot": 1,
            "allow_video_specs": False,
            "allow_reference_assets": False,
            "continuity_strength": "high",
            "prompt_detail_level": "balanced",
            "negative_prompt_enabled": True,
            "target_width": width,
            "target_height": height,
            "safe_content_only": True,
        },
    }


def _client_request_id(request: LocalMvpRequest, profile: dict[str, object]) -> str:
    payload = {
        "mode": request.mode,
        "prompt": request.prompt,
        "title": request.title,
        "project_id": request.project_id,
        "profile": profile,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"local-simulated-e2e:{hashlib.sha256(encoded).hexdigest()}"


def _progress_outcome(before: ProductionJob, current: ProductionJob) -> str:
    if before.status is ProductionJobStatus.QUEUED:
        return "started"
    if current.status is ProductionJobStatus.FAILED:
        return "failed"
    if current.status is ProductionJobStatus.WAITING_FOR_RETRY:
        return "retry_scheduled"
    if current.status is ProductionJobStatus.NEEDS_USER_ACTION:
        return "manual_intervention"
    if current.status in {ProductionJobStatus.CANCEL_REQUESTED, ProductionJobStatus.CANCELLED}:
        return "cancelled"
    return "succeeded"


def _succeeded_stage_count(events: tuple[object, ...]) -> int:
    return len({item.stage for item in events if isinstance(item, ProductionStageSucceeded)})


def _recommended_action(status: ProductionJobStatus, code: str) -> str:
    if status is ProductionJobStatus.WAITING_FOR_RETRY:
        return "Wait until the durable retry time, then resume the same job."
    if status is ProductionJobStatus.NEEDS_USER_ACTION:
        return "Resolve the reported local condition, then use the existing retry service."
    if status in {ProductionJobStatus.CANCEL_REQUESTED, ProductionJobStatus.CANCELLED}:
        return "Create a new job if the cancelled production is still required."
    if code in {"local_iteration_limit_reached", "local_worker_no_progress"}:
        return "Inspect the current durable stage and resume the same job."
    return "Inspect the durable stage error; do not reset or delete completed artifacts."


def _local_cost_summary(summary: ProductionJobCostSummary) -> LocalMvpCostSummary:
    return LocalMvpCostSummary(
        scripting_accounted_cost_usd=str(
            summary.category(JobCostCategory.SCRIPTING).accounted_cost_usd
        ),
        tts_accounted_cost_usd=str(
            summary.category(JobCostCategory.SPEECH).accounted_cost_usd
        ),
        fitting_accounted_cost_usd=str(
            summary.category(JobCostCategory.NARRATION_FITTING).accounted_cost_usd
        ),
        image_accounted_cost_usd=str(
            summary.category(JobCostCategory.IMAGES).accounted_cost_usd
        ),
        video_accounted_cost_usd=str(
            summary.category(JobCostCategory.VIDEO).accounted_cost_usd
        ),
        total_accounted_cost_usd=str(summary.total_accounted_cost_usd),
        total_reported_cost_usd=str(summary.total_reported_cost_usd),
        total_estimated_cost_usd=str(summary.total_estimated_cost_usd),
        reported_cost_coverage_percent=str(summary.reported_cost_coverage_percent),
        fully_reported=summary.fully_reported,
        fingerprint=summary.fingerprint,
    )


def _file_identity(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError("LONG_FORM_RENDER is empty")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            digest.update(chunk)
    return size, digest.hexdigest()


_UNSUCCESSFUL_STATUSES = frozenset(
    {
        ProductionJobStatus.FAILED,
        ProductionJobStatus.WAITING_FOR_RETRY,
        ProductionJobStatus.NEEDS_USER_ACTION,
        ProductionJobStatus.CANCEL_REQUESTED,
        ProductionJobStatus.CANCELLED,
    }
)
_STOPPED_STATUSES = frozenset(
    {
        ProductionJobStatus.FAILED,
        ProductionJobStatus.WAITING_FOR_RETRY,
        ProductionJobStatus.NEEDS_USER_ACTION,
        ProductionJobStatus.CANCEL_REQUESTED,
        ProductionJobStatus.CANCELLED,
    }
)
