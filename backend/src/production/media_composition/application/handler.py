"""Durable BUILDING_TIMELINE handler with idempotent recovery."""

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
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.models import (
    CompositionManifestStatus,
    MediaCompositionManifest,
    MediaCompositionPlan,
)
from backend.src.production.media_composition.domain.timeline import (
    build_media_composition_plan,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionConflictError,
    MediaCompositionCorruptError,
    MediaCompositionPlanError,
    MediaCompositionSourceError,
    MediaCompositionStalePlanError,
    MediaCompositionStorageError,
)
from backend.src.production.media_composition.paths import (
    media_composition_manifest_relative_path,
)
from backend.src.production.media_composition.ports import (
    CompositionClock,
    MediaCompositionSourceReader,
    MediaCompositionStore,
)
from backend.src.production.media_composition.recovery import project_manifest
from backend.src.production.media_composition.serialization import (
    serialize_media_composition_manifest,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class MediaCompositionHandler:
    supported_stages = frozenset({ProductionStage.BUILDING_TIMELINE})

    def __init__(
        self,
        *,
        source_reader: MediaCompositionSourceReader,
        store: MediaCompositionStore,
        configuration: MediaCompositionConfiguration,
        clock: CompositionClock,
    ) -> None:
        self._source_reader = source_reader
        self._store = store
        self._configuration = configuration
        self._clock = clock

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        started_at = self._aware_now()
        if command.stage not in self.supported_stages or context.stage != command.stage:
            raise ValueError("media composition handler received another stage")
        if context.command_id != command.command_id:
            raise ValueError("media composition context does not match command")
        try:
            source = await self._source_reader.read(context=context)
            proposed = build_media_composition_plan(source, self._configuration)
            existing_plan = await self._store.read_plan(context=context)
            if (
                existing_plan is not None
                and existing_plan.plan_fingerprint != proposed.plan_fingerprint
            ):
                raise MediaCompositionStalePlanError(
                    "durable composition plan differs from current sources"
                )
            plan = existing_plan or proposed
            plan_path, plan_size, plan_sha = await self._store.write_plan(
                context=context,
                plan=plan,
            )
            existing_manifest = await self._store.read_manifest(context=context)
            if existing_manifest is not None and (
                existing_manifest.plan_fingerprint != plan.plan_fingerprint
                or existing_manifest.source_fingerprint != plan.source_fingerprint
                or existing_manifest.plan_sha256 != plan_sha
            ):
                raise MediaCompositionStalePlanError("composition manifest identity differs")
            manifest = project_manifest(
                plan=plan,
                source=source,
                attempt_number=context.attempt_number,
                plan_relative_path=plan_path,
                plan_sha256=plan_sha,
                plan_size_bytes=plan_size,
                now=self._aware_now(),
                existing=existing_manifest,
            )
            if existing_manifest is None:
                await self._store.create_manifest(
                    context=context,
                    manifest=manifest,
                )
            elif manifest != existing_manifest:
                await self._store.checkpoint_manifest(
                    context=context,
                    previous=existing_manifest,
                    current=manifest,
                )
            if manifest.status is not CompositionManifestStatus.COMPLETE:
                return self._failure(
                    command,
                    started_at,
                    StageOutcome.NEEDS_USER_ACTION,
                    "media_composition_asset_unavailable",
                )
            return self._success(command, context, plan, manifest, started_at)
        except asyncio.CancelledError:
            raise
        except MediaCompositionStalePlanError:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "media_composition_stale_plan",
            )
        except MediaCompositionConflictError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                "media_composition_checkpoint_conflict",
                retry_after_seconds=1,
            )
        except MediaCompositionSourceError:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "media_composition_source_invalid",
            )
        except (
            MediaCompositionCorruptError,
            MediaCompositionPlanError,
            MediaCompositionStorageError,
            ValueError,
        ):
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "media_composition_invalid",
            )

    def _success(
        self,
        command: StageCommand,
        context: StageContext,
        plan: MediaCompositionPlan,
        manifest: MediaCompositionManifest,
        started_at: datetime,
    ) -> StageExecutionOutput:
        plan_content_path = manifest.plan_relative_path
        plan_size = manifest.plan_size_bytes
        plan_sha = manifest.plan_sha256
        manifest_content = serialize_media_composition_manifest(manifest)
        artifacts = (
            Artifact(
                artifact_id=_artifact_id(
                    command.job_id,
                    context.attempt_number,
                    "plan",
                ),
                job_id=command.job_id,
                artifact_type=ArtifactType.MEDIA_COMPOSITION_PLAN,
                relative_path=plan_content_path,
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=plan_size,
                sha256=plan_sha,
                provider="orion-media-composition",
                model_version=plan.schema_version,
                metadata={
                    "plan_fingerprint": plan.plan_fingerprint,
                    "timeline_checksum": plan.timeline_checksum,
                    "renderer_executed": False,
                },
            ),
            Artifact(
                artifact_id=_artifact_id(
                    command.job_id,
                    context.attempt_number,
                    "manifest",
                ),
                job_id=command.job_id,
                artifact_type=ArtifactType.MEDIA_COMPOSITION_MANIFEST,
                relative_path=media_composition_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(manifest_content),
                sha256=hashlib.sha256(manifest_content).hexdigest(),
                provider="orion-media-composition",
                model_version=manifest.schema_version,
                metadata={
                    "asset_count": len(plan.assets),
                    "plan_fingerprint": plan.plan_fingerprint,
                    "status": manifest.status.value,
                    "timeline_checksum": plan.timeline_checksum,
                    "renderer_executed": False,
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
                    "asset_count": len(plan.assets),
                    "handler": type(self).__name__,
                    "renderer_executed": False,
                    "timeline_checksum": plan.timeline_checksum,
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
                error_message=(
                    "Media composition requires verified durable inputs"
                    if outcome is StageOutcome.NEEDS_USER_ACTION
                    else "Media composition did not complete"
                ),
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "renderer_executed": False,
                },
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("media composition clock must be timezone-aware")
        return value


def _artifact_id(job_id: UUID, attempt_number: int, kind: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:media-composition:{job_id}:{attempt_number}:{kind}",
    )
