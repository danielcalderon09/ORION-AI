"""Durable RENDERING_LONG_FORM handler for dry-run and local FFmpeg."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import (
    RenderingConflictError,
    RenderingCorruptError,
    RenderingError,
    RenderingProcessError,
    RenderingProcessTimeoutError,
    RenderingRequestError,
    RenderingSourceError,
    RenderingStaleSourceError,
    RenderingUnexpectedOutputError,
    RenderingValidationError,
)
from backend.src.production.rendering.execution_plan import (
    build_ffmpeg_execution_plan,
)
from backend.src.production.rendering.models import (
    FFmpegExecutionPlan,
    FFmpegRenderResult,
    LocalRenderRequest,
    RendererKind,
    RenderExecutionManifest,
    RenderManifestStatus,
)
from backend.src.production.rendering.paths import (
    render_execution_manifest_relative_path,
)
from backend.src.production.rendering.ports import (
    LocalRenderer,
    LocalRenderStore,
    RenderClock,
    RenderCompositionSourceReader,
)
from backend.src.production.rendering.recovery import (
    cancelled_manifest,
    failed_manifest,
    prepare_manifest,
    ready_to_render_manifest,
    rendering_manifest,
    validate_manifest_identity,
    validated_manifest,
    validating_manifest,
)
from backend.src.production.rendering.request_builder import build_local_render_request
from backend.src.production.rendering.serialization import (
    serialize_render_execution_manifest,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class LocalRenderPreparationHandler:
    supported_stages = frozenset({ProductionStage.RENDERING_LONG_FORM})

    def __init__(
        self,
        *,
        source_reader: RenderCompositionSourceReader,
        store: LocalRenderStore,
        renderer: LocalRenderer,
        configuration: RenderingConfiguration,
        clock: RenderClock,
    ) -> None:
        if renderer.renderer_kind is not configuration.renderer:
            raise ValueError("configured renderer differs from local renderer")
        self._source_reader = source_reader
        self._store = store
        self._renderer = renderer
        self._configuration = configuration
        self._clock = clock

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        started_at = self._aware_now()
        if command.stage not in self.supported_stages or context.stage != command.stage:
            raise ValueError("local render handler received another stage")
        if context.command_id != command.command_id or context.job_id != command.job_id:
            raise ValueError("local render context does not match command")
        manifest: RenderExecutionManifest | None = None
        try:
            source = await self._source_reader.read(context=context)
            proposed = build_local_render_request(source, self._configuration)
            existing_request = await self._store.read_request(context=context)
            if existing_request is not None and not _requests_compatible(
                existing_request,
                proposed,
            ):
                raise RenderingStaleSourceError(
                    "durable local render request belongs to another source plan"
                )
            request = existing_request or proposed
            request_path, request_size, request_sha = await self._store.write_request(
                context=context,
                request=request,
            )
            execution_plan, plan_artifact = await self._execution_plan(
                request=request,
                source_plan=source.plan,
                context=context,
            )
            manifest = await self._store.read_manifest(context=context)
            if manifest is None:
                manifest = prepare_manifest(
                    request=request,
                    attempt_number=context.attempt_number,
                    capabilities=self._renderer.capabilities,
                    now=self._aware_now(),
                )
                await self._store.create_manifest(context=context, manifest=manifest)
            else:
                validate_manifest_identity(
                    manifest,
                    request=request,
                    capabilities=self._renderer.capabilities,
                )
            if manifest.status is RenderManifestStatus.VALIDATED:
                await self._validate_replay_output(manifest)
                return self._success(
                    command=command,
                    context=context,
                    request=request,
                    manifest=manifest,
                    request_artifact=(request_path, request_size, request_sha),
                    plan_artifact=plan_artifact,
                    started_at=started_at,
                )
            if manifest.status in {
                RenderManifestStatus.INVALID,
                RenderManifestStatus.FAILED,
            }:
                raise RenderingValidationError("durable render state is terminally unsuccessful")
            if await self._store.output_exists(
                relative_path=request.requested_output.relative_path
            ):
                raise RenderingUnexpectedOutputError(
                    "final render output exists without a validated manifest"
                )
            if request.renderer_kind is RendererKind.DRY_RUN:
                if manifest.status is RenderManifestStatus.PREPARED:
                    manifest = await self._checkpoint(
                        context,
                        manifest,
                        validating_manifest(manifest, now=self._aware_now()),
                    )
            else:
                if manifest.status in {
                    RenderManifestStatus.PREPARED,
                    RenderManifestStatus.CANCELLED,
                }:
                    manifest = await self._checkpoint(
                        context,
                        manifest,
                        ready_to_render_manifest(manifest, now=self._aware_now()),
                    )
                if manifest.status is RenderManifestStatus.READY_TO_RENDER:
                    manifest = await self._checkpoint(
                        context,
                        manifest,
                        rendering_manifest(manifest, now=self._aware_now()),
                    )
                if manifest.status not in {
                    RenderManifestStatus.RENDERING,
                    RenderManifestStatus.PROBING,
                }:
                    raise RenderingValidationError("FFmpeg manifest cannot resume safely")
            if request.renderer_kind is RendererKind.DRY_RUN:
                result = await self._renderer.prepare_or_validate(request)
            else:
                result = await self._renderer.prepare_or_validate(request, execution_plan)
            output_artifact_id = (
                _artifact_id(command.job_id, context.attempt_number, "output")
                if isinstance(result, FFmpegRenderResult)
                else None
            )
            checkpoint = validated_manifest(
                manifest,
                result=result,
                output_artifact_id=output_artifact_id,
                now=self._aware_now(),
            )
            await self._store.checkpoint_manifest(
                context=context,
                previous=manifest,
                current=checkpoint,
            )
            return self._success(
                command=command,
                context=context,
                request=request,
                manifest=checkpoint,
                request_artifact=(request_path, request_size, request_sha),
                plan_artifact=plan_artifact,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            if manifest is not None and manifest.status in {
                RenderManifestStatus.READY_TO_RENDER,
                RenderManifestStatus.RENDERING,
                RenderManifestStatus.PROBING,
            }:
                checkpoint = cancelled_manifest(manifest, now=self._aware_now())
                await asyncio.shield(
                    self._store.checkpoint_manifest(
                        context=context,
                        previous=manifest,
                        current=checkpoint,
                    )
                )
            raise
        except RenderingUnexpectedOutputError:
            return self._failure(
                command, started_at, StageOutcome.NEEDS_USER_ACTION, "render_output_conflict"
            )
        except RenderingStaleSourceError:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "render_preparation_stale_source",
            )
        except RenderingSourceError:
            return self._failure(
                command, started_at, StageOutcome.NEEDS_USER_ACTION, "render_source_invalid"
            )
        except RenderingProcessTimeoutError:
            await self._persist_failure(context, manifest, "ffmpeg_timeout")
            return self._failure(
                command, started_at, StageOutcome.FAILED_TRANSIENT, "ffmpeg_timeout"
            )
        except RenderingProcessError as exc:
            await self._persist_failure(context, manifest, exc.code)
            return self._failure(command, started_at, StageOutcome.FAILED_TRANSIENT, exc.code)
        except RenderingConflictError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                "render_checkpoint_conflict",
                retry_after_seconds=1,
            )
        except (
            RenderingCorruptError,
            RenderingRequestError,
            RenderingValidationError,
            RenderingError,
            ValueError,
        ):
            await self._persist_failure(context, manifest, "render_invalid")
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "render_preparation_invalid",
            )

    async def _execution_plan(
        self,
        *,
        request: LocalRenderRequest,
        source_plan: object,
        context: StageContext,
    ) -> tuple[FFmpegExecutionPlan | None, tuple[str, int, str] | None]:
        if request.renderer_kind is RendererKind.DRY_RUN:
            if await self._store.read_execution_plan(context=context) is not None:
                raise RenderingConflictError("dry-run attempt contains an FFmpeg plan")
            return None, None
        from backend.src.production.media_composition.domain.models import MediaCompositionPlan

        if not isinstance(source_plan, MediaCompositionPlan):
            raise RenderingRequestError("render source is not a composition plan")
        proposed = build_ffmpeg_execution_plan(
            request,
            source_plan,
            context,
            self._configuration,
        )
        existing = await self._store.read_execution_plan(context=context)
        if existing is not None and existing.argument_fingerprint != (
            proposed.argument_fingerprint
        ):
            raise RenderingStaleSourceError("durable FFmpeg execution plan is stale")
        plan = existing or proposed
        artifact = await self._store.write_execution_plan(context=context, plan=plan)
        return plan, artifact

    async def _validate_replay_output(self, manifest: RenderExecutionManifest) -> None:
        exists = await self._store.output_exists(relative_path=manifest.output_relative_path)
        if manifest.renderer_kind is RendererKind.DRY_RUN:
            if exists:
                raise RenderingUnexpectedOutputError("dry-run output unexpectedly exists")
            return
        if not exists or manifest.output_size_bytes is None or manifest.output_sha256 is None:
            raise RenderingUnexpectedOutputError("validated render output is missing")
        identity = await self._store.output_identity(relative_path=manifest.output_relative_path)
        if identity != (manifest.output_size_bytes, manifest.output_sha256):
            raise RenderingUnexpectedOutputError("validated render output identity differs")

    async def _checkpoint(
        self,
        context: StageContext,
        previous: RenderExecutionManifest,
        current: RenderExecutionManifest,
    ) -> RenderExecutionManifest:
        await self._store.checkpoint_manifest(context=context, previous=previous, current=current)
        return current

    async def _persist_failure(
        self,
        context: StageContext,
        manifest: RenderExecutionManifest | None,
        code: str,
    ) -> None:
        if manifest is None or manifest.status not in {
            RenderManifestStatus.READY_TO_RENDER,
            RenderManifestStatus.RENDERING,
            RenderManifestStatus.PROBING,
        }:
            return
        checkpoint = failed_manifest(manifest, now=self._aware_now(), code=code)
        try:
            await self._store.checkpoint_manifest(
                context=context, previous=manifest, current=checkpoint
            )
        except RenderingError:
            return

    def _success(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        request: LocalRenderRequest,
        manifest: RenderExecutionManifest,
        request_artifact: tuple[str, int, str],
        plan_artifact: tuple[str, int, str] | None,
        started_at: datetime,
    ) -> StageExecutionOutput:
        media_produced = manifest.media_produced
        contract_provider = (
            "orion-local-render-preparation"
            if request.renderer_kind is RendererKind.DRY_RUN
            else "orion-local-render"
        )
        artifacts: list[Artifact] = [
            Artifact(
                artifact_id=_artifact_id(command.job_id, context.attempt_number, "request"),
                job_id=command.job_id,
                artifact_type=ArtifactType.LOCAL_RENDER_REQUEST,
                relative_path=request_artifact[0],
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=request_artifact[1],
                sha256=request_artifact[2],
                provider=contract_provider,
                model_version=request.schema_version,
                metadata={
                    "media_produced": media_produced,
                    "renderer_kind": request.renderer_kind.value,
                    "request_fingerprint": request.request_fingerprint,
                },
            )
        ]
        if plan_artifact is not None:
            artifacts.append(
                Artifact(
                    artifact_id=_artifact_id(command.job_id, context.attempt_number, "ffmpeg-plan"),
                    job_id=command.job_id,
                    artifact_type=ArtifactType.FFMPEG_EXECUTION_PLAN,
                    relative_path=plan_artifact[0],
                    mime_type="application/json",
                    status=ArtifactStatus.READY,
                    size_bytes=plan_artifact[1],
                    sha256=plan_artifact[2],
                    provider="orion-local-ffmpeg",
                    model_version="1.0.0",
                    metadata={
                        "request_fingerprint": request.request_fingerprint,
                        "shell": False,
                    },
                )
            )
        manifest_content = serialize_render_execution_manifest(manifest)
        artifacts.append(
            Artifact(
                artifact_id=_artifact_id(command.job_id, context.attempt_number, "manifest"),
                job_id=command.job_id,
                artifact_type=ArtifactType.RENDER_EXECUTION_MANIFEST,
                relative_path=render_execution_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(manifest_content),
                sha256=hashlib.sha256(manifest_content).hexdigest(),
                provider=contract_provider,
                model_version=manifest.schema_version,
                metadata={
                    "media_produced": media_produced,
                    "renderer_kind": manifest.renderer_kind.value,
                    "request_fingerprint": manifest.request_fingerprint,
                    "status": manifest.status.value,
                },
            )
        )
        result = manifest.ffmpeg_result
        if result is not None:
            if manifest.output_artifact_id is None:
                raise ValueError("validated FFmpeg manifest has no output artifact ID")
            artifacts.append(
                Artifact(
                    artifact_id=manifest.output_artifact_id,
                    job_id=command.job_id,
                    artifact_type=ArtifactType.LONG_FORM_RENDER,
                    relative_path=result.output_relative_path,
                    mime_type="video/mp4",
                    status=ArtifactStatus.READY,
                    size_bytes=result.output_size_bytes,
                    sha256=result.output_sha256,
                    provider="orion-local-ffmpeg",
                    model_version=result.renderer_version,
                    metadata={
                        "audio_codec": result.audio_codec,
                        "duration_ms": result.duration_ms,
                        "frame_rate_denominator": result.frame_rate_denominator,
                        "frame_rate_numerator": result.frame_rate_numerator,
                        "height": result.height,
                        "media_produced": True,
                        "pixel_format": result.pixel_format,
                        "renderer_kind": "ffmpeg",
                        "request_fingerprint": request.request_fingerprint,
                        "source_plan_fingerprint": request.source_plan_fingerprint,
                        "timeline_checksum": request.timeline_checksum,
                        "validated_by_ffprobe": True,
                        "video_codec": result.video_codec,
                        "width": result.width,
                    },
                )
            )
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=100,
                output_artifact_ids=tuple(item.artifact_id for item in artifacts),
                metadata={
                    "future_output_path": request.requested_output.relative_path,
                    "handler": type(self).__name__,
                    "media_produced": media_produced,
                    "preparation_validated": True,
                    "real_renderer_executed": media_produced,
                    "renderer_kind": request.renderer_kind.value,
                    "request_fingerprint": request.request_fingerprint,
                    "source_plan_fingerprint": request.source_plan_fingerprint,
                    "timeline_checksum": request.timeline_checksum,
                    "validated_by_ffprobe": media_produced,
                },
            ),
            artifacts=tuple(artifacts),
        )

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        code: str,
        *,
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
                error_code=code,
                error_message="Local render did not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "media_produced": False,
                    "real_renderer_executed": False,
                },
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("render clock must be timezone-aware")
        return value


def _artifact_id(job_id: UUID, attempt_number: int, kind: str) -> UUID:
    namespace = (
        "orion:local-render-preparation"
        if kind in {"request", "manifest"}
        else "orion:local-render"
    )
    return uuid5(NAMESPACE_URL, f"{namespace}:{job_id}:{attempt_number}:{kind}")


def _requests_compatible(
    existing: LocalRenderRequest,
    proposed: LocalRenderRequest,
) -> bool:
    if existing.request_fingerprint == proposed.request_fingerprint:
        return True
    if (
        existing.schema_version != "1.0.0"
        or existing.renderer_kind is not RendererKind.DRY_RUN
        or proposed.renderer_kind is not RendererKind.DRY_RUN
    ):
        return False
    core_existing = (
        existing.job_id,
        existing.source_plan_artifact_id,
        existing.source_plan_relative_path,
        existing.source_plan_sha256,
        existing.source_plan_fingerprint,
        existing.timeline_checksum,
        existing.expected_duration_ms,
        existing.expected_duration_frames,
        existing.output_width,
        existing.output_height,
        existing.frame_rate_numerator,
        existing.frame_rate_denominator,
        existing.track_summary,
        existing.requested_output,
        tuple(
            (item.asset_id, item.relative_path, item.sha256, item.fingerprint)
            for item in existing.asset_fingerprints
        ),
    )
    core_proposed = (
        proposed.job_id,
        proposed.source_plan_artifact_id,
        proposed.source_plan_relative_path,
        proposed.source_plan_sha256,
        proposed.source_plan_fingerprint,
        proposed.timeline_checksum,
        proposed.expected_duration_ms,
        proposed.expected_duration_frames,
        proposed.output_width,
        proposed.output_height,
        proposed.frame_rate_numerator,
        proposed.frame_rate_denominator,
        proposed.track_summary,
        proposed.requested_output,
        tuple(
            (item.asset_id, item.relative_path, item.sha256, item.fingerprint)
            for item in proposed.asset_fingerprints
        ),
    )
    return core_existing == core_proposed
