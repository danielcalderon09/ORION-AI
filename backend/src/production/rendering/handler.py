"""Durable RENDERING_LONG_FORM preparation handler."""

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
    RenderingRequestError,
    RenderingSourceError,
    RenderingStaleSourceError,
    RenderingUnexpectedOutputError,
    RenderingValidationError,
)
from backend.src.production.rendering.models import (
    LocalRenderRequest,
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
    prepare_manifest,
    validate_manifest_identity,
    validated_manifest,
    validating_manifest,
)
from backend.src.production.rendering.request_builder import (
    build_local_render_request,
)
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
        try:
            source = await self._source_reader.read(context=context)
            proposed = build_local_render_request(source, self._configuration)
            existing_request = await self._store.read_request(context=context)
            if (
                existing_request is not None
                and existing_request.request_fingerprint != proposed.request_fingerprint
            ):
                raise RenderingStaleSourceError(
                    "durable local render request belongs to another source plan"
                )
            request = existing_request or proposed
            request_path, request_size, request_sha = await self._store.write_request(
                context=context,
                request=request,
            )
            if await self._store.output_exists(
                relative_path=request.requested_output.relative_path
            ):
                raise RenderingUnexpectedOutputError(
                    "future render output exists and overwrite is forbidden"
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
                return self._success(
                    command,
                    context,
                    request,
                    manifest,
                    request_path,
                    request_size,
                    request_sha,
                    started_at,
                )
            if manifest.status in {
                RenderManifestStatus.INVALID,
                RenderManifestStatus.FAILED,
            }:
                raise RenderingValidationError(
                    "durable render validation is terminally unsuccessful"
                )
            if manifest.status is RenderManifestStatus.PREPARED:
                checkpoint = validating_manifest(manifest, now=self._aware_now())
                await self._store.checkpoint_manifest(
                    context=context,
                    previous=manifest,
                    current=checkpoint,
                )
                manifest = checkpoint
            result = await self._renderer.prepare_or_validate(request)
            checkpoint = validated_manifest(
                manifest,
                result=result,
                now=self._aware_now(),
            )
            await self._store.checkpoint_manifest(
                context=context,
                previous=manifest,
                current=checkpoint,
            )
            return self._success(
                command,
                context,
                request,
                checkpoint,
                request_path,
                request_size,
                request_sha,
                started_at,
            )
        except asyncio.CancelledError:
            raise
        except RenderingUnexpectedOutputError:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "render_output_conflict",
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
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "render_source_invalid",
            )
        except RenderingConflictError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                "render_preparation_checkpoint_conflict",
                retry_after_seconds=1,
            )
        except (
            RenderingCorruptError,
            RenderingRequestError,
            RenderingValidationError,
            RenderingError,
            ValueError,
        ):
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "render_preparation_invalid",
            )

    def _success(
        self,
        command: StageCommand,
        context: StageContext,
        request: LocalRenderRequest,
        manifest: RenderExecutionManifest,
        request_path: str,
        request_size: int,
        request_sha: str,
        started_at: datetime,
    ) -> StageExecutionOutput:
        manifest_content = serialize_render_execution_manifest(manifest)
        artifacts = (
            Artifact(
                artifact_id=_artifact_id(
                    command.job_id,
                    context.attempt_number,
                    "request",
                ),
                job_id=command.job_id,
                artifact_type=ArtifactType.LOCAL_RENDER_REQUEST,
                relative_path=request_path,
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=request_size,
                sha256=request_sha,
                provider="orion-local-render-preparation",
                model_version=request.schema_version,
                metadata={
                    "media_produced": False,
                    "renderer_kind": request.renderer_kind.value,
                    "request_fingerprint": request.request_fingerprint,
                },
            ),
            Artifact(
                artifact_id=_artifact_id(
                    command.job_id,
                    context.attempt_number,
                    "manifest",
                ),
                job_id=command.job_id,
                artifact_type=ArtifactType.RENDER_EXECUTION_MANIFEST,
                relative_path=render_execution_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(manifest_content),
                sha256=hashlib.sha256(manifest_content).hexdigest(),
                provider="orion-local-render-preparation",
                model_version=manifest.schema_version,
                metadata={
                    "media_produced": False,
                    "renderer_kind": manifest.renderer_kind.value,
                    "request_fingerprint": manifest.request_fingerprint,
                    "status": manifest.status.value,
                },
            ),
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
                    "media_produced": False,
                    "preparation_validated": True,
                    "real_renderer_executed": False,
                    "renderer_kind": request.renderer_kind.value,
                    "request_fingerprint": request.request_fingerprint,
                    "source_plan_fingerprint": request.source_plan_fingerprint,
                    "timeline_checksum": request.timeline_checksum,
                },
            ),
            artifacts=artifacts,
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
                error_message="Local render preparation did not validate",
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
            raise ValueError("render preparation clock must be timezone-aware")
        return value


def _artifact_id(job_id: UUID, attempt_number: int, kind: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:local-render-preparation:{job_id}:{attempt_number}:{kind}",
    )
